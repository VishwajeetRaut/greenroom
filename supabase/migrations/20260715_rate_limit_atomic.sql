-- Make the rate-limit check atomic.
--
-- services/rate_limit.py previously counted rows in the window and then, in a
-- separate round-trip, inserted the current request. Those two statements are
-- not atomic: concurrent requests all read a count below the limit before any
-- of them inserts, so every one is admitted. A burst of 20 against a limit of
-- 5 let all 20 through — the limiter did nothing under exactly the condition it
-- exists to stop.
--
-- No client-side arrangement of two round-trips can fix that, so the check
-- moves into a single function call here.
--
-- The advisory lock is what provides the atomicity. Read-committed snapshots
-- alone are not enough: two concurrent transactions would still both see a
-- pre-insert count. pg_advisory_xact_lock serialises callers on the same
-- user_id and releases at transaction end, so per-user checks queue up while
-- different users stay fully parallel.

CREATE OR REPLACE FUNCTION check_rate_limit(
    p_user_id        UUID,
    p_max            INT,
    p_window_seconds INT DEFAULT 60
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INT;
BEGIN
    -- Serialise concurrent checks for this user only.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

    SELECT count(*) INTO v_count
    FROM rate_limit_events
    WHERE user_id = p_user_id
      AND ts >= now() - make_interval(secs => p_window_seconds);

    IF v_count >= p_max THEN
        RETURN FALSE;  -- caller raises 429; nothing recorded
    END IF;

    INSERT INTO rate_limit_events (user_id) VALUES (p_user_id);
    RETURN TRUE;
END;
$$;

-- Callable only by the service role (the backend). Revoke the default grant
-- that would otherwise let anon/authenticated clients drive the limiter.
REVOKE ALL ON FUNCTION check_rate_limit(UUID, INT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION check_rate_limit(UUID, INT, INT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION check_rate_limit(UUID, INT, INT) TO service_role;
