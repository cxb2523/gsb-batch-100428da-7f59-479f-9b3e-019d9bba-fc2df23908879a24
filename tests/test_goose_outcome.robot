*** Settings ***
Library    ${EXECDIR}/automation-resources/AIAgentLibrary.py
Library    ${EXECDIR}/tests/FakeGooseLibrary.py
Library    OperatingSystem
Resource   ${EXECDIR}/automation-resources/generic-resources.robot
Test Setup    Use Fake Goose Output    clean goose answer about Edgar Allan Poe
Test Teardown    Restore Real Subprocess

*** Variables ***
${AI_AGENT_OUTPUT_FOLDER}    tests/rf-offline-output
${TEST_REPORT_STEM}    local-agent-report9
${DOCKER_TEST_STEM}    docker-agent1-report9
## Fast retry settings for offline tests; production keeps ${RETRY}/${DELAY}.
${TEST_RETRIES}    3
${TEST_INTERVAL}    0

*** Keywords ***
Collect Test Local Outcome
    [Arguments]    ${PROMPT}    ${STEM}    ${TYPE}=User_Prompt
    IF    "${TYPE}" == "User_Prompt"
        ${OUTCOME}=    Collect Local AI Agent Prompt Outcome    ${PROMPT}    ${STEM}    ${TEST_RETRIES}    ${TEST_INTERVAL}
    ELSE
        ${OUTCOME}=    Collect Local AI Agent Instruction File Outcome    ${PROMPT}    ${STEM}    ${TEST_RETRIES}    ${TEST_INTERVAL}
    END
    RETURN    ${OUTCOME}

Collect Test Docker Outcome
    [Arguments]    ${PROMPT}    ${STEM}
    ${OUTCOME}=    Collect Docker AI Agent Outcome    docker-goose-cli-test    ${PROMPT}    extension-one    extension-two    ${STEM}    ${TEST_RETRIES}    ${TEST_INTERVAL}
    RETURN    ${OUTCOME}

*** Test Cases ***

Successful Goose Call Keeps Output And Writes A Report
    Use Fake Goose Output    clean goose answer about Edgar Allan Poe
    ${OUTCOME}=    Collect Test Local Outcome    Who was Edgar Allan Poe?    ${TEST_REPORT_STEM}
    Handle Agent Outcome    ${OUTCOME}    ${TEST_REPORT_STEM}
    Should Be Equal    ${OUTCOME}[status]    SUCCESS
    Should Be Equal    ${OUTPUT}    clean goose answer about Edgar Allan Poe
    Should Be Equal As Integers    ${OUTCOME}[attempt_count]    1
    ${REPORT_PATH}=    Persist Local Agent Report    ${EXECDIR}/tests/rf-offline-output    9    rf-test-timestamp    ${OUTPUT}
    File Should Exist    ${REPORT_PATH}
    ${REPORT_CONTENT}=    Get File    ${REPORT_PATH}
    Should Contain    ${REPORT_CONTENT}    clean goose answer about Edgar Allan Poe

Goose Output With Known Error Signature Produces An Outcome File
    Use Fake Goose Known Error    Rate limit hit. Retrying in 4s
    ${OUTCOME}=    Collect Test Local Outcome    a rate limited prompt    ${TEST_REPORT_STEM}
    Handle Agent Outcome    ${OUTCOME}    ${TEST_REPORT_STEM}
    Should Be Equal    ${OUTCOME}[status]    KNOWN_ERROR
    Should Contain    ${OUTCOME}[reason]    Rate limit hit
    Should Be Equal As Integers    ${OUTCOME}[attempt_count]    3
    ${OUTCOME_PATH}=    Persist Agent Outcome File    ${OUTCOME}    ${EXECDIR}/tests/rf-offline-output    ${TEST_REPORT_STEM}    rf-error-timestamp
    File Should Exist    ${OUTCOME_PATH}
    Should Contain    ${OUTCOME_PATH}    -outcome-
    ${OUTCOME_CONTENT}=    Get File    ${OUTCOME_PATH}
    Should Contain    ${OUTCOME_CONTENT}    KNOWN_ERROR
    Should Contain    ${OUTCOME_CONTENT}    Rate limit hit
    Should Contain    ${OUTCOME_CONTENT}    Attempt history

Retry Exhausted By Timeout Produces An Outcome File With Partial Output
    Use Fake Goose Timeout    partial output captured before the hang
    ${OUTCOME}=    Collect Test Docker Outcome    some prompt    ${DOCKER_TEST_STEM}
    Handle Agent Outcome    ${OUTCOME}    ${DOCKER_TEST_STEM}
    Should Be Equal    ${OUTCOME}[status]    RETRY_EXHAUSTED
    Should Contain    ${OUTCOME}[reason]    timed out
    Should Be Equal    ${OUTCOME}[output]    partial output captured before the hang
    Should Be Equal As Integers    ${OUTCOME}[attempt_count]    3
    ${OUTCOME_PATH}=    Persist Agent Outcome File    ${OUTCOME}    ${EXECDIR}/tests/rf-offline-output    ${DOCKER_TEST_STEM}    rf-timeout-timestamp
    File Should Exist    ${OUTCOME_PATH}
    ${OUTCOME_CONTENT}=    Get File    ${OUTCOME_PATH}
    Should Contain    ${OUTCOME_CONTENT}    RETRY_EXHAUSTED
    Should Contain    ${OUTCOME_CONTENT}    partial output captured before the hang
    Should Contain    ${OUTCOME_CONTENT}    yes

Retry Exhausted By Missing Goose Process Is Reported With Empty Raw Output
    Use Fake Goose Missing Process
    ${OUTCOME}=    Collect Test Local Outcome    a prompt with no goose binary    ${TEST_REPORT_STEM}
    Handle Agent Outcome    ${OUTCOME}    ${TEST_REPORT_STEM}
    Should Be Equal    ${OUTCOME}[status]    RETRY_EXHAUSTED
    Should Contain    ${OUTCOME}[reason]    could not be run
    Should Be Equal    ${OUTCOME}[output]    ${EMPTY}
    ${OUTCOME_PATH}=    Persist Agent Outcome File    ${OUTCOME}    ${EXECDIR}/tests/rf-offline-output    ${TEST_REPORT_STEM}    rf-missing-timestamp
    File Should Exist    ${OUTCOME_PATH}
    ${OUTCOME_CONTENT}=    Get File    ${OUTCOME_PATH}
    Should Contain    ${OUTCOME_CONTENT}    (empty)

Local And Docker Paths Share The Same Known Error Rules
    Use Fake Goose Known Error    Tool not found: remember_memory
    ${LOCAL_OUTCOME}=    Collect Test Local Outcome    local prompt    ${TEST_REPORT_STEM}
    Restore Real Subprocess
    Use Fake Goose Known Error    Tool not found: remember_memory
    ${DOCKER_OUTCOME}=    Collect Test Docker Outcome    docker prompt    ${DOCKER_TEST_STEM}
    Should Be Equal    ${LOCAL_OUTCOME}[status]    KNOWN_ERROR
    Should Be Equal    ${DOCKER_OUTCOME}[status]    KNOWN_ERROR
    Should Be Equal    ${LOCAL_OUTCOME}[markers]    ${DOCKER_OUTCOME}[markers]
    Should Contain    ${DOCKER_OUTCOME}[markers][0]    Tool not found

Outcome Files Never Overwrite Existing Reports Or Outcomes
    Use Fake Goose Known Error    ERROR: provider unavailable
    ${OUTCOME}=    Collect Test Local Outcome    repeated prompt    ${TEST_REPORT_STEM}
    ${FIRST}=    Persist Agent Outcome File    ${OUTCOME}    ${EXECDIR}/tests/rf-offline-output    ${TEST_REPORT_STEM}    shared-timestamp
    ${SECOND}=    Persist Agent Outcome File    ${OUTCOME}    ${EXECDIR}/tests/rf-offline-output    ${TEST_REPORT_STEM}    shared-timestamp
    Should Not Be Equal    ${FIRST}    ${SECOND}
    File Should Exist    ${FIRST}
    File Should Exist    ${SECOND}
    ${FIRST_CONTENT}=    Get File    ${FIRST}
    Should Contain    ${FIRST_CONTENT}    KNOWN_ERROR
