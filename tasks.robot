*** Settings ***
Documentation       Processes PDF invoices with Amazon Textract.
...                 Saves the extracted invoice data in an Excel file.
...                 A per-file outcome report is written to the output directory,
...                 so failed or timed out invoices are visible instead of
...                 blocking the rest of the batch.

Resource            invoices.resource


*** Variables ***
${PROCESSING_JOBS}      ${EMPTY}
${INVOICES_SAVED}       ${FALSE}


*** Tasks ***
Process PDF invoices with Amazon Textract
    [Setup]    Initialize Amazon Clients
    ${empty_jobs}=    Create List
    Set Suite Variable    ${PROCESSING_JOBS}    ${empty_jobs}
    Set Suite Variable    ${INVOICES_SAVED}    ${FALSE}
    ${jobs}=    Create Job Records For Bucket Files
    Set Suite Variable    ${PROCESSING_JOBS}    ${jobs}
    ${invoices}=    Wait For PDF Processing Results    ${jobs}
    Save Processing Outcomes    ${jobs}
    ${save_status}    ${save_result}=    Run Keyword And Ignore Error
    ...    Save Invoices To Excel    ${invoices}
    IF    "${save_status}" == "FAIL"
        Fail    The invoice Excel file could not be written: ${save_result}
    END
    Set Suite Variable    ${INVOICES_SAVED}    ${TRUE}
    [Teardown]    Delete Files From Amazon S3 Bucket    ${PROCESSING_JOBS}    ${INVOICES_SAVED}

Create Invoices
    [Setup]    Initialize Amazon Clients
    Create Invoices

Delete Files From Amazon S3 Bucket
    [Setup]    Initialize Amazon Clients
    Delete Files From Amazon S3 Bucket
