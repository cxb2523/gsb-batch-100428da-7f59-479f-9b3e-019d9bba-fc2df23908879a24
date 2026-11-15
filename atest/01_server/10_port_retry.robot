*** Settings ***
Documentation       Verify Jupyter server startup survives a lost port race.

Library             JupyterLibrary

Suite Setup         Create Directory    ${LOGS}
Suite Teardown      Terminate All Jupyter Servers

Force Tags          server


*** Variables ***
${LOGS}     ${OUTPUT_DIR}${/}${OS}${/}${BROWSER}${/}logs${/}


*** Test Cases ***
Server Moves To Another Port When The Port Is Taken
    [Documentation]    A port claimed after probing must not stop the server.
    [Setup]    Occupy One Port
    Set Jupyter Server Port Candidates    ${BUSY_PORT}    ${FREE_PORT}
    ${nbserver} =    Start New Jupyter Server    token=port-retry-token
    ${ready} =    Wait For Jupyter Server To Be Ready    ${nbserver}
    Should Be Equal As Integers    ${ready}    1    msg=The restarted server should be ready
    ${port} =    Get Jupyter Server Port    ${nbserver}
    Should Be Equal As Integers    ${port}    ${FREE_PORT}
    ...    msg=The server should have moved past the occupied port
    ${url} =    Get Jupyter Server URL    ${nbserver}
    Should Be Equal    ${url}    http://127.0.0.1:${FREE_PORT}/@rf/
    ${token} =    Get Jupyter Server Token    ${nbserver}
    Should Be Equal    ${token}    port-retry-token
    [Teardown]    Release All The Occupied Ports

Retries Exhausted Report The Attempted Ports
    [Documentation]    Giving up must raise an error that can be acted upon.
    [Setup]    Occupy Two Ports
    Set Jupyter Server Port Candidates    ${BUSY_PORT}    ${BUSY_PORT_2}
    ${error} =    Run Keyword And Expect Error    Failed to start Jupyter server*
    ...    Start New Jupyter Server    token=port-retry-token    port_retries=1
    Should Contain    ${error}    after 2 attempt(s)
    ...    msg=The error should count the attempts
    Should Contain    ${error}    ${BUSY_PORT}, ${BUSY_PORT_2}
    ...    msg=The error should list every port that was tried
    Should Contain    ${error}    Last failure on port ${BUSY_PORT_2}
    ...    msg=The error should name the port of the last failure
    Should Contain    ${error}    already in use
    ...    msg=The error should quote the server's own reason
    [Teardown]    Release All The Occupied Ports

An Explicit Port Is Never Replaced
    [Documentation]    A requested port must fail fast rather than silently move.
    [Setup]    Occupy One Port
    ${error} =    Run Keyword And Expect Error    Could not start Jupyter server*
    ...    Start New Jupyter Server    token=port-retry-token    port=${BUSY_PORT}
    Should Contain    ${error}    on port ${BUSY_PORT}
    Should Contain    ${error}    given explicitly
    ...    msg=The error should say the port is not changed
    Should Contain    ${error}    already in use
    ...    msg=The error should keep the underlying reason
    [Teardown]    Release All The Occupied Ports

The Port The Server Really Bound Is Recorded
    [Documentation]    Caller-supplied arguments may let the server move ports.
    ...    When that happens, the handle must describe the server that exists,
    ...    not the port that was asked for.
    [Setup]    Occupy One Port
    ${nbserver} =    Start New Jupyter Server    jupyter-notebook    ${None}    /@rf/    ${None}    port-retry-token
    ...    --no-browser    --port\=${BUSY_PORT}
    ...    --NotebookApp.token\=port-retry-token    --NotebookApp.base_url\=/@rf/
    ${ready} =    Wait For Jupyter Server To Be Ready    ${nbserver}
    Should Be Equal As Integers    ${ready}    1
    ${port} =    Get Jupyter Server Port    ${nbserver}
    Should Not Be Equal As Integers    ${port}    ${BUSY_PORT}
    ...    msg=The recorded port must not be the one that was unavailable
    ${url} =    Get Jupyter Server URL    ${nbserver}
    Should Contain    ${url}    :${port}/
    ${token} =    Get Jupyter Server Token    ${nbserver}
    Should Be Equal    ${token}    port-retry-token
    [Teardown]    Release All The Occupied Ports


*** Keywords ***
Occupy One Port
    [Documentation]    Bind and listen on a port, and pick a different free one.
    ${sock}    ${busy} =    Occupy A Port
    ${free} =    Find A Free Port Other Than    ${busy}
    @{sockets} =    Create List    ${sock}
    Set Test Variable    ${SOCKETS}    ${sockets}
    Set Test Variable    ${BUSY_PORT}    ${busy}
    Set Test Variable    ${FREE_PORT}    ${free}

Occupy Two Ports
    [Documentation]    Bind and listen on two distinct ports.
    ${sock_1}    ${busy_1} =    Occupy A Port
    ${sock_2}    ${busy_2} =    Occupy A Port Other Than    ${busy_1}
    @{sockets} =    Create List    ${sock_1}    ${sock_2}
    Set Test Variable    ${SOCKETS}    ${sockets}
    Set Test Variable    ${BUSY_PORT}    ${busy_1}
    Set Test Variable    ${BUSY_PORT_2}    ${busy_2}

Occupy A Port
    [Documentation]    Return a listening socket and the port it holds.
    ${sock} =    Evaluate    __import__("socket").socket()
    ${port} =    Evaluate
    ...    ($sock.bind(("127.0.0.1", 0)), $sock.listen(1), $sock.getsockname()[1])[2]
    RETURN    ${sock}    ${port}

Occupy A Port Other Than
    [Documentation]    Occupy a port that differs from an already occupied one.
    [Arguments]    ${excluded}
    FOR    ${attempt}    IN RANGE    50
        ${sock}    ${port} =    Occupy A Port
        IF    $port != $excluded    RETURN    ${sock}    ${port}
        Evaluate    $sock.close()
    END
    Fail    Could not occupy a port other than ${excluded}

Find A Free Port Other Than
    [Documentation]    Ask the library for a free port that differs from an excluded one.
    [Arguments]    ${excluded}
    FOR    ${attempt}    IN RANGE    50
        ${port} =    Get Unused Port
        IF    $port != $excluded    RETURN    ${port}
    END
    Fail    Could not find a free port other than ${excluded}

Release All The Occupied Ports
    [Documentation]    Stop any server and close every listening socket.
    Terminate All Jupyter Servers
    Run Keyword And Ignore Error    Evaluate    [$sock.close() for $sock in $SOCKETS]
