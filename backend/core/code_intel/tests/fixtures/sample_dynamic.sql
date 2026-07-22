CREATE OR REPLACE PROCEDURE run_recon IS
    sqlText varchar2(1000);
BEGIN
    -- historical: sqlText := 'DROP TABLE old_ghost'; (removed, must NOT be an edge)
    sqlText := 'CREATE TABLE recon_staging AS SELECT * FROM src_a';
    execute immediate(sqlText);
    sqlText := 'UPDATE recon_result SET status = ''X''';
    execute immediate(sqlText);
    sqlText := 'INSERT INTO audit_log VALUES (1)';
    execute immediate(sqlText);
END run_recon;
/

CREATE OR REPLACE PROCEDURE audit_log IS
BEGIN
    NULL;
END audit_log;
/

CREATE OR REPLACE PROCEDURE guard_msg IS
    msg varchar2(200);
BEGIN
    msg := 'CREATE TABLE was blocked by policy';
    msg := 'UPDATE the record failed';
    raise_application_error(-20001, msg);
END guard_msg;
/
