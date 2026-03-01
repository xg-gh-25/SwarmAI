# Implementation Plan: Fast Startup Optimization

## Overview

This implementation plan converts the fast startup optimization design into discrete coding tasks. The approach is incremental: first add the database schema, then create the InitializationManager, modify the startup flow, add the reset endpoint, and finally update the status endpoint.

## Tasks

- [x] 1. Add initialization_complete flag to database schema
  - [x] 1.1 Add migration for initialization_complete column in app_settings table
    - Add column `initialization_complete INTEGER DEFAULT 0` to app_settings table
    - Add migration in `_run_migrations()` method
    - _Requirements: 1.1, 1.4_
  
  - [ ] 1.2 Write property test for flag persistence round-trip
    - **Property 1: Flag Persistence Round-Trip**
    - **Validates: Requirements 1.1, 1.3, 1.4**

- [x] 2. Create InitializationManager component
  - [x] 2.1 Create `backend/core/initialization_manager.py` with core methods
    - Implement `is_initialization_complete()` method
    - Implement `set_initialization_complete()` method
    - Implement `get_initialization_status()` method
    - _Requirements: 1.1, 1.2, 1.3_
  
  - [x] 2.2 Implement `run_quick_validation()` method
    - Check if default agent exists in database
    - Check if default workspace exists in database
    - Return True only if both exist
    - _Requirements: 3.2, 3.3, 3.4_
  
  - [ ] 2.3 Write property test for quick validation resource checks
    - **Property 4: Quick Validation Resource Checks**
    - **Validates: Requirements 3.2, 3.3, 3.4**
  
  - [x] 2.4 Implement `run_full_initialization()` method
    - Call existing `_register_default_skills()` function
    - Call existing `_register_default_mcp_servers()` function
    - Call existing `ensure_default_agent()` function
    - Call existing `ensure_default_workspace()` function
    - Set initialization_complete to true only if all critical steps succeed
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6_
  
  - [ ] 2.5 Write property test for full initialization creates all resources
    - **Property 2: Full Initialization Creates All Resources**
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5**
  
  - [ ] 2.6 Write property test for failure preserves incomplete state
    - **Property 7: Failure Preserves Incomplete State**
    - **Validates: Requirements 2.6, 6.5**

- [x] 3. Checkpoint - Ensure InitializationManager tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Modify startup flow in main.py
  - [x] 4.1 Update lifespan handler to use InitializationManager
    - Import InitializationManager
    - Check initialization_complete flag on startup
    - Run quick validation if flag is true
    - Run full initialization if flag is false or quick validation fails
    - _Requirements: 2.1, 3.1, 3.6_
  
  - [ ] 4.2 Write property test for initialization mode selection
    - **Property 3: Initialization Mode Selection**
    - **Validates: Requirements 2.1, 3.1**
  
  - [ ] 4.3 Write property test for missing resources trigger full initialization
    - **Property 5: Missing Resources Trigger Full Initialization**
    - **Validates: Requirements 3.6, 6.4**

- [x] 5. Add reset_to_defaults endpoint
  - [x] 5.1 Implement `reset_to_defaults()` method in InitializationManager
    - Set initialization_complete to false
    - Trigger full initialization
    - Return status with success/error details
    - _Requirements: 4.2, 4.3, 4.4, 4.5_
  
  - [x] 5.2 Add POST `/api/system/reset-to-defaults` endpoint in system.py
    - Call InitializationManager.reset_to_defaults()
    - Return appropriate response
    - _Requirements: 4.1_
  
  - [ ] 5.3 Write property test for reset clears flag and triggers full init
    - **Property 6: Reset Clears Flag and Triggers Full Init**
    - **Validates: Requirements 4.2, 4.3**

- [x] 6. Update system status endpoint
  - [x] 6.1 Add initialization_mode and initialization_complete fields to SystemStatusResponse
    - Add `initialization_mode: str` field (first_run, quick_validation, reset)
    - Add `initialization_complete: bool` field
    - Update get_system_status() to populate new fields
    - _Requirements: 5.1, 5.2, 5.3_
  
  - [ ] 6.2 Write property test for status reflects initialization state
    - **Property 9: Status Reflects Initialization State**
    - **Validates: Requirements 5.3**

- [x] 7. Checkpoint - Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Refactor existing initialization functions
  - [x] 8.1 Extract skill/MCP registration from ensure_default_agent()
    - Move `_register_default_skills()` call to InitializationManager
    - Move `_register_default_mcp_servers()` call to InitializationManager
    - Keep ensure_default_agent() focused on agent creation only
    - _Requirements: 2.2, 2.3_
  
  - [x] 8.2 Update ensure_default_agent() to skip registration when called from quick validation
    - Add optional parameter to skip skill/MCP registration
    - Only register skills/MCPs during full initialization
    - _Requirements: 3.4_

- [x] 9. Add error handling and retry logic
  - [x] 9.1 Implement database retry with exponential backoff
    - Add retry decorator or utility function
    - Configure max 3 retries with 100ms, 200ms, 400ms delays
    - _Requirements: 6.1_
  
  - [ ] 9.2 Write property test for database retry on unavailability
    - **Property 8: Database Retry on Unavailability**
    - **Validates: Requirements 6.1**

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The implementation uses Python with pytest and hypothesis for property-based testing
