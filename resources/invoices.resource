*** Settings ***
Resource    common.resource


*** Variables ***
${S3_BUCKET_NAME}                 example-amazon-textract-pdf-invoice-bucket
${NUMBER_OF_INVOICES}             %{NUMBER_OF_INVOICES=10}
${TEXTRACT_MAX_WAIT_ROUNDS}       %{TEXTRACT_MAX_WAIT_ROUNDS=90}
${TEXTRACT_POLL_INTERVAL}         %{TEXTRACT_POLL_INTERVAL=3s}
${INVOICE_FILE_NAME}              invoices.xlsx
${PROCESSING_OUTCOME_FILE}        processing_outcomes.csv
${CLEANUP_OUTCOME_FILE}           cleanup_outcomes.csv


*** Keywords ***
Process PDF with Amazon Textract
    [Arguments]    ${filename}
    ${job_id}=    Start Document Analysis    ${S3_BUCKET_NAME}    ${filename}
    RETURN    ${job_id}

Create Job Records For Bucket Files
    [Documentation]    Start one Amazon Textract analysis job per S3 file and
    ...    keep a processing record per file.
    ...
    ...    A file whose analysis cannot be started is recorded as failed right
    ...    away, so a single bad file does not prevent the rest of the batch
    ...    from being submitted.
    ${filenames}=    Get File Keys From Amazon S3 Bucket
    ${jobs}=    Create List
    FOR    ${filename}    IN    @{filenames}
        ${job}=    Create Job Record    ${filename}
        ${submit_status}    ${job_id}=    Run Keyword And Ignore Error
        ...    Process PDF with Amazon Textract    ${filename}
        IF    "${submit_status}" == "PASS"
            ${has_job_id}=    Has Entries    ${job_id}
            IF    ${has_job_id}
                Set To Dictionary    ${job}    job_id=${job_id}
            ELSE
                Set To Dictionary    ${job}
                ...    status=FAILED
                ...    reason=Could not start the Amazon Textract analysis: no job identifier was returned
            END
        ELSE
            Set To Dictionary    ${job}
            ...    status=FAILED
            ...    reason=Could not start the Amazon Textract analysis: ${job_id}
        END
        Append To List    ${jobs}    ${job}
    END
    RETURN    ${jobs}

Wait For PDF Processing Results
    [Documentation]    Poll every Amazon Textract job and collect the invoices
    ...    which were analysed successfully.
    ...
    ...    A job which still is not ``SUCCEEDED`` after the configured number of
    ...    wait rounds is recorded as ``TIMEOUT``, and a job which Amazon
    ...    Textract reports as ``FAILED`` is recorded as ``FAILED``. Neither
    ...    outcome blocks the remaining jobs: only successful responses are
    ...    turned into invoice models, and the returned list keeps the order of
    ...    the S3 file keys.
    [Arguments]    ${jobs}    ${max_rounds}=${TEXTRACT_MAX_WAIT_ROUNDS}
    ...    ${poll_interval}=${TEXTRACT_POLL_INTERVAL}
    ${jobs}=    Normalize Processing Jobs    ${jobs}
    ${max_rounds}=    To Round Count    ${max_rounds}
    FOR    ${round_index}    IN RANGE    ${max_rounds}
        ${pending}=    Get Pending Processing Jobs    ${jobs}
        ${has_pending}=    Has Entries    ${pending}
        IF    ${has_pending}
            FOR    ${job}    IN    @{pending}
                Poll Single Textract Job    ${job}
            END
            ${still_pending}=    Get Pending Processing Jobs    ${jobs}
            ${has_still_pending}=    Has Entries    ${still_pending}
            IF    ${has_still_pending}
                ${more_rounds}=    Evaluate    ${round_index} + 1 < ${max_rounds}
                IF    ${more_rounds}    Sleep    ${poll_interval}
            END
        END
    END
    Mark Pending Jobs As Timed Out    ${jobs}    ${max_rounds}
    ${invoices}=    Collect Successful Invoices    ${jobs}
    RETURN    ${invoices}

Poll Single Textract Job
    [Documentation]    Poll one job and store its state on the job record.
    ...
    ...    A polling error is remembered and the job is polled again on the next
    ...    round, so one failed request does not discard an otherwise healthy
    ...    job. A job which keeps failing until the last round ends up as
    ...    ``TIMEOUT`` with the polling error as part of its reason.
    [Arguments]    ${job}
    ${job_id}=    Read Job Field    ${job}    job_id
    ${poll_status}    ${response}=    Run Keyword And Ignore Error
    ...    Get Document Analysis    ${job_id}
    IF    "${poll_status}" == "FAIL"
        Set To Dictionary    ${job}    last_status=ERROR    last_error=${response}
        RETURN
    END
    ${job_status}=    Get Job Status    ${response}
    Set To Dictionary    ${job}    last_status=${job_status}    last_error=${EMPTY}
    IF    "${job_status}" == "SUCCEEDED"
        Mark Job Succeeded    ${job}    ${response}
    ELSE IF    "${job_status}" == "FAILED"
        ${message}=    Get Response Field    ${response}    StatusMessage    ${EMPTY}
        ${reason}=    Build Failed Job Reason    ${message}
        Set To Dictionary    ${job}    status=FAILED    reason=${reason}
    END

Mark Job Succeeded
    [Documentation]    Convert a successful Textract response into invoice models.
    ...
    ...    Every page which contains form fields produces one invoice, as
    ...    before. A response from which no invoice can be extracted is reported
    ...    as a failure of that file, so the file is not treated as processed.
    [Arguments]    ${job}    ${response}
    ${convert_status}    ${model}=    Run Keyword And Ignore Error
    ...    Convert Textract Response To Model    ${response}
    ${model_missing}=    Is Missing    ${model}
    IF    "${convert_status}" == "FAIL" or ${model_missing}
        Set To Dictionary    ${job}
        ...    status=FAILED
        ...    reason=The Amazon Textract response could not be converted into an invoice model
        RETURN
    END
    ${extract_status}    ${invoices}=    Run Keyword And Ignore Error
    ...    Extract Invoices From Model    ${model}
    IF    "${extract_status}" == "FAIL"
        Set To Dictionary    ${job}
        ...    status=FAILED
        ...    reason=The invoice could not be read from the Amazon Textract response: ${invoices}
        RETURN
    END
    ${has_invoices}=    Has Entries    ${invoices}
    IF    not ${has_invoices}
        Set To Dictionary    ${job}
        ...    status=FAILED
        ...    reason=No invoice fields could be found in the Amazon Textract response
        RETURN
    END
    Set To Dictionary    ${job}    status=SUCCEEDED    reason=${EMPTY}    invoices=${invoices}

Extract Invoices From Model
    [Arguments]    ${model}
    ${invoices}=    Create List
    FOR    ${page}    IN    @{model.pages}
        ${has_fields}=    Has Entries    ${page.form.fields}
        IF    ${has_fields}
            ${invoice_status}    ${invoice}=    Run Keyword And Ignore Error
            ...    Create AWS Invoice    ${page.form.fields}
            IF    "${invoice_status}" == "PASS"
                Append To List    ${invoices}    ${invoice}
            END
        END
    END
    RETURN    ${invoices}

Mark Pending Jobs As Timed Out
    [Arguments]    ${jobs}    ${max_rounds}
    FOR    ${job}    IN    @{jobs}
        ${status}=    Read Job Field    ${job}    status
        ${is_pending}=    Is Processing Status    ${status}
        IF    ${is_pending}
            ${last_status}=    Read Job Field    ${job}    last_status
            ${last_error}=    Read Job Field    ${job}    last_error
            ${reason}=    Build Timeout Reason    ${max_rounds}    ${last_status}    ${last_error}
            Set To Dictionary    ${job}    status=TIMEOUT    reason=${reason}
        END
    END

Save Invoices To Excel
    [Documentation]    Write the successfully extracted invoices to the workbook.
    ...
    ...    The workbook always contains the header row, even when no invoice
    ...    could be extracted from the whole batch.
    [Arguments]    ${invoices}
    ${rows}=    Build Invoice Worksheet Rows    ${invoices}
    Create Workbook    ${OUTPUT_DIR}${/}${INVOICE_FILE_NAME}
    Append Rows To Worksheet    ${rows}
    Save Workbook

Save Processing Outcomes
    [Documentation]    Write the per-file outcome report of the batch.
    ...
    ...    Every file of the batch appears in the report, including the files
    ...    which were processed successfully. The report is written even when
    ...    the bucket was empty, so the output directory always documents what
    ...    happened to each file.
    [Arguments]    ${jobs}    ${file_name}=${PROCESSING_OUTCOME_FILE}
    ${path}=    Set Variable    ${OUTPUT_DIR}${/}${file_name}
    ${jobs}=    Normalize Processing Jobs    ${jobs}
    Write Processing Outcomes    ${path}    ${jobs}
    ${summary}=    Build Outcome Summary    ${jobs}
    Log    ${summary}
    RETURN    ${path}

Delete Files From Amazon S3 Bucket
    [Documentation]    Delete invoice files from the S3 bucket without aborting
    ...    on individual failures.
    ...
    ...    When the processing records of the batch are given, only files which
    ...    were analysed successfully are deleted. Files which failed or timed
    ...    out stay in the bucket for a retry and are reported as skipped, so
    ...    they are never silently dropped as if they had been processed.
    ...
    ...    When the batch is cleaned up after processing, the files are only
    ...    removed if the invoice workbook was written (``invoices_saved``), so a
    ...    failed export does not throw away the only copy of the results.
    ...    Without records the task deletes every object of the bucket.
    [Arguments]    ${jobs}=${NONE}    ${invoices_saved}=${TRUE}
    ${delete_all}=    Is Missing    ${jobs}
    IF    ${delete_all}
        ${keys}=    Get File Keys From Amazon S3 Bucket
        ${jobs}=    Create Cleanup Job Records    ${keys}
    ELSE
        ${jobs}=    Normalize Processing Jobs    ${jobs}
        Finalize Unfinished Jobs    ${jobs}
        ${workbook_saved}=    Convert To Boolean    ${invoices_saved}
        IF    not ${workbook_saved}
            Mark Invoices Not Saved    ${jobs}
        END
    END
    FOR    ${job}    IN    @{jobs}
        ${may_delete}=    Should Delete Job File    ${delete_all}    ${job}
        IF    ${may_delete}
            ${filename}=    Read Job Field    ${job}    filename
            ${delete_status}    ${deleted}=    Run Keyword And Ignore Error
            ...    Delete Invoice File From Amazon S3 Bucket    ${filename}
            IF    "${delete_status}" == "FAIL"
                Set To Dictionary    ${job}
                ...    cleanup_status=DELETE_FAILED
                ...    cleanup_reason=${deleted}
                Log    Could not delete ${filename}: ${deleted}    WARN
            ELSE
                ${delete_failed}=    Delete Failed    ${deleted}
                IF    ${delete_failed}
                    Set To Dictionary    ${job}
                    ...    cleanup_status=DELETE_FAILED
                    ...    cleanup_reason=Amazon S3 did not confirm the deletion of ${filename}
                    Log    Amazon S3 did not confirm the deletion of ${filename}    WARN
                ELSE
                    Set To Dictionary    ${job}    cleanup_status=DELETED    cleanup_reason=${EMPTY}
                END
            END
        ELSE
            Skip Job Cleanup    ${job}
        END
    END
    IF    ${delete_all}
        Save Processing Outcomes    ${jobs}    ${CLEANUP_OUTCOME_FILE}
    ELSE
        Save Processing Outcomes    ${jobs}
    END

Delete Invoice File From Amazon S3 Bucket
    [Arguments]    ${filename}
    ${files}=    Create List    ${filename}
    ${deleted}=    Delete Files    ${S3_BUCKET_NAME}    ${files}
    ${delete_failed}=    Delete Failed    ${deleted}
    IF    ${delete_failed}
        Fail    Amazon S3 did not confirm the deletion of ${filename}
    END
    RETURN    ${deleted}

Skip Job Cleanup
    [Documentation]    Keep the file in the bucket and record why.
    [Arguments]    ${job}
    ${reason}=    Record Skipped Cleanup    ${job}
    ${filename}=    Read Job Field    ${job}    filename
    Log    Keeping ${filename} in the bucket: ${reason}    WARN

Create PDF Invoice
    [Arguments]    ${index}
    ${filename}=    Set Variable    invoice_${index}.pdf
    ${file_path}=    Set Variable    ${OUTPUT_DIR}${/}${filename}
    Create Random Invoice    ${file_path}    ${index}
    ${upload_status}=    Upload File
    ...    ${S3_BUCKET_NAME}
    ...    ${file_path}
    ...    ${filename}

Create Invoices
    ${bucket_created}=    Create Bucket    ${S3_BUCKET_NAME}
    FOR    ${index}    IN RANGE    1    ${NUMBER_OF_INVOICES}+1
        Create PDF Invoice    ${index}
    END

Get File Keys From Amazon S3 Bucket
    ${files}=    List Files    ${S3_BUCKET_NAME}
    @{keys}=    Create List
    FOR    ${file}    IN    @{files}
        Append To List    ${keys}    ${file}[Key]
    END
    RETURN    ${keys}
