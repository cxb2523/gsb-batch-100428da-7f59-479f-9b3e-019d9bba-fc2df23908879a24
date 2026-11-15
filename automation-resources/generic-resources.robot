*** Settings ***
Library                   ${EXECDIR}/automation-resources/AIAgentLibrary.py
Library                   OperatingSystem
Library                   Process
Library                   String

*** Variables ***
${RETRY}    100
${DELAY}    2
## Per Goose invocation hard cap in seconds (mirrors ${LLM_TIME_CONSTRAINT}).
${GOOSE_CALL_TIMEOUT}    240
## Suite timestamps are set by the task suites; the default keeps standalone
## keyword calls working.
${TIMESTAMP}    standalone
${GOOSE_AI_LOCAL_OUTPUT_COLLECTOR}    put all of that into the ${EXECDIR} folder.
${GOOSE_AI_DOCKER_OUTPUT_COLLECTOR}    put all of that into the /root/workspace/ai-agent-output folder and create the folder if it doesn't exist. Do not ask me permission to create the folder just do it.

*** Keywords ***

Collect Docker Goose AI Outcome
    [Arguments]    ${DOCKER_CONTAINER_NAME}    ${GOOSE_AI_PROMPT}    ${GOOSE_AI_EXTENSION1}    ${GOOSE_AI_EXTENSION2}    ${REPORT_STEM}
    ## The retry count, interval and success/error rules all live in AIAgentLibrary
    ## and goose_outcome.py, which the local path uses as well, so the two paths
    ## cannot drift apart.
    ${OUTCOME}=    Collect Docker AI Agent Outcome    ${DOCKER_CONTAINER_NAME}    ${GOOSE_AI_PROMPT}    ${GOOSE_AI_EXTENSION1}    ${GOOSE_AI_EXTENSION2}    ${REPORT_STEM}    ${RETRY}    ${DELAY}
    RETURN    ${OUTCOME}

Collect Local Goose AI Outcome
    [Arguments]    ${GOOSE_AI_PROMPT}    ${REPORT_STEM}    ${INSTRUCTION_TYPE}=User_Prompt
    IF    "${INSTRUCTION_TYPE}" == "User_Prompt"
        ${OUTCOME}=    Collect Local AI Agent Prompt Outcome    ${GOOSE_AI_PROMPT}    ${REPORT_STEM}    ${RETRY}    ${DELAY}
    ELSE
        ${OUTCOME}=    Collect Local AI Agent Instruction File Outcome    ${GOOSE_AI_PROMPT}    ${REPORT_STEM}    ${RETRY}    ${DELAY}
    END
    RETURN    ${OUTCOME}

Handle Agent Outcome
    [Arguments]    ${OUTCOME}    ${REPORT_STEM}
    ## Every call ends here: success keeps the output, failure lands an outcome
    ## file next to the reports and the task continues instead of aborting the run.
    Set Suite Variable    ${AGENT_OUTCOME}    ${OUTCOME}
    Set Suite Variable    ${OUTPUT}    ${OUTCOME}[output]
    ${OUTCOME_STATUS}=    Set Variable    ${OUTCOME}[status]
    Log to Console    ...
    Log to Console    Goose agent outcome status: ${OUTCOME_STATUS}
    Log to Console    Reason: ${OUTCOME}[reason]
    IF    $OUTCOME['status'] != 'SUCCESS'
        ${OUTPUT_DIR}=    Set Variable    ${EXECDIR}/${AI_AGENT_OUTPUT_FOLDER}
        ${OUTCOME_PATH}=    Persist Agent Outcome File    ${OUTCOME}    ${OUTPUT_DIR}    ${REPORT_STEM}    ${TIMESTAMP}
        Log to Console    Failed outcome (raw output preserved) written to: ${OUTCOME_PATH}
        Log    Goose agent call failed with status '${OUTCOME_STATUS}': ${OUTCOME}[reason]    level=WARN
    ELSE
        Log to Console    Goose agent call succeeded after ${OUTCOME}[attempt_count] attempt(s).
    END

Run Goose AI Based On Instruction Type
    [Arguments]    ${GOOSE_AI_PROMPT}    ${INSTRUCTION_TYPE}    ${REPORT_STEM}
    IF    "${INSTRUCTION_TYPE}" == "User_Prompt"
        ${OUTCOME}=    Collect Local Goose AI Outcome    ${GOOSE_AI_PROMPT}    ${REPORT_STEM}    User_Prompt
    ELSE IF    "${INSTRUCTION_TYPE}" == "Markdown_File"
        ${INSTRUCTION_FILE}=    Set Variable    ${EXECDIR}/agent-instructions/detailed-instructions/${GOOSE_AI_PROMPT}
        ${OUTCOME}=    Collect Local Goose AI Outcome    ${INSTRUCTION_FILE}    ${REPORT_STEM}    Markdown_File
    END
    Handle Agent Outcome    ${OUTCOME}    ${REPORT_STEM}

Create Local Agent Report
    [Arguments]    ${REPORT_NUMBER}
    ## Only successful runs produce a report; the non-clobbering helper keeps the
    ## existing local-agent-report<N>-<timestamp>.md naming and never overwrites.
    ${OUTPUT_DIR}=    Set Variable    ${EXECDIR}/${AI_AGENT_OUTPUT_FOLDER}
    ${REPORT_PATH}=    Persist Local Agent Report    ${OUTPUT_DIR}    ${REPORT_NUMBER}    ${TIMESTAMP}    ${OUTPUT}
    Log to Console    Local agent report written to: ${REPORT_PATH}

Display Goose AI Logs
    Log to Console    ...
    Log to Console    ...
    Log to Console    ...
    Log to Console    ${OUTPUT}
    Log to Console    ...
    Log to Console    ...
    Log to Console    ...
    Log    ${OUTPUT}
