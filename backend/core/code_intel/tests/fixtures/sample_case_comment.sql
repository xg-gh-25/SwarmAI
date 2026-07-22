CREATE OR REPLACE PROCEDURE proc_main IS
    v_sql VARCHAR2(200);
BEGIN
    -- historical: log_run('OLD', 0);   was removed
    v_sql := 'BEGIN log_run(:1); END;';   /* dynamic sql, not a real call */
    log_run('SUCCESS', 1);   -- ONLY real call; lowercase vs mixed-case def below
END proc_main;
/

CREATE OR REPLACE PROCEDURE Log_Run (p_status IN VARCHAR2, n IN NUMBER) IS
BEGIN
    INSERT INTO run_log VALUES (p_status, n);
END Log_Run;
/
