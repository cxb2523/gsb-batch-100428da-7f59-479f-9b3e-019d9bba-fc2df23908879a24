*** Settings ***
Resource                  ${EXECDIR}/automation-resources/generic-resources.robot
Library                   DateTime
Library                   OperatingSystem
Library                   Process
Library                   String
Library     DataDriver    ${EXECDIR}/agent-instructions/docker-agent1-distributed-instructions.csv    dialect=unix    include=${INCLUDED_TAG}
Test Template    Run Tasks From Goose AI Agent

*** Variables ***

${AI_AGENT_OUTPUT_FOLDER}    automation-resources/ai-agent-output-collector/ai-agent-output

*** Tasks ***

DISTRIBUTED RPA AI-AGENT1 TASKS : Run datadriven AI-Agent tasks with this data -> container_name = ${DOCKER_CONTAINER_NAME}, prompt_message = ${GOOSE_AI_PROMPT}, docker_agent1_report_number = ${REPORT_NUMBER}

*** Keywords ***

Run Tasks From Goose AI Agent
    [Arguments]    ${DOCKER_CONTAINER_NAME}    ${GOOSE_AI_PROMPT}    ${REPORT_NUMBER}
    ${TIMESTAMP}=    Get Current Date    result_format=%Y_%m_%d_%H-%M-%S-%f
    Set Suite Variable    ${TIMESTAMP}
    ${REPORT_STEM}=    Set Variable    docker-agent1-report${REPORT_NUMBER}
    ${OUTCOME}=    Collect Docker Goose AI Outcome    ${DOCKER_CONTAINER_NAME}    ${GOOSE_AI_PROMPT} write a detailed summary of that work in an docker-agent1-report${REPORT_NUMBER}-${TIMESTAMP}.md file and ${GOOSE_AI_DOCKER_OUTPUT_COLLECTOR}    npx -y @modelcontextprotocol/server-memory    npx -y @modelcontextprotocol/server-filesystem /root/workspace    ${REPORT_STEM}
    Handle Agent Outcome    ${OUTCOME}    ${REPORT_STEM}
    Display Goose AI Logs
