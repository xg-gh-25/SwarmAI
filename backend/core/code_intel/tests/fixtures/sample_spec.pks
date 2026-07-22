CREATE OR REPLACE PACKAGE recon_pkg AS
    PROCEDURE proc_daily (p_date IN DATE);
    FUNCTION compute_total (y IN NUMBER) RETURN NUMBER;
END recon_pkg;
/
