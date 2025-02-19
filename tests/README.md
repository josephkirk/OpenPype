Automatic tests for OpenPype
============================
Structure:
- integration - end to end tests, slow (see README.md in the integration folder for more info)
    - openpype/modules/MODULE_NAME - structure follow directory structure in code base
        - fixture - sample data `(MongoDB dumps, test files etc.)`
        - `tests.py` - single or more pytest files for MODULE_NAME
- unit - quick unit test 
    - MODULE_NAME   
        - fixture
        - `tests.py`
    
How to run:
----------
- single test class could be run by PyCharm and its pytest runner directly
- OR
- use Openpype command 'runtests' from command line
-- `${OPENPYPE_ROOT}/start.py runtests`
  
By default, this command will run all tests in ${OPENPYPE_ROOT}/tests.

Specific location could be provided to this command as an argument, either as absolute path, or relative path to ${OPENPYPE_ROOT}.
(eg. `${OPENPYPE_ROOT}/start.py runtests ../tests/integration`) will trigger only tests in `integration` folder.

See `${OPENPYPE_ROOT}/cli.py:runtests` for other arguments.
