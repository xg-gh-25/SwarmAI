import { useState, useCallback, useRef } from 'react';
import type { ErrorResponse, LoadingState, LoadingStateInfo } from '../types';
import { extractErrorResponse } from '../services/api';

interface UseLoadingStateOptions {
  initialState?: LoadingState;
  onError?: (error: ErrorResponse) => void;
}

interface UseLoadingStateReturn extends LoadingStateInfo {
  setLoading: () => void;
  setSuccess: () => void;
  setError: (error: unknown) => void;
  reset: () => void;
  isIdle: boolean;
  isLoading: boolean;
  isSuccess: boolean;
  isError: boolean;
  execute: <T>(fn: () => Promise<T>) => Promise<T | undefined>;
}

export function useLoadingState(options: UseLoadingStateOptions = {}): UseLoadingStateReturn {
  const { initialState = 'idle', onError } = options;

  const [state, setState] = useState<LoadingState>(initialState);
  const [error, setErrorState] = useState<ErrorResponse | undefined>(undefined);
  const mountedRef = useRef(true);

  const setLoading = useCallback(() => {
    setState('loading');
    setErrorState(undefined);
  }, []);

  const setSuccess = useCallback(() => {
    if (mountedRef.current) {
      setState('success');
      setErrorState(undefined);
    }
  }, []);

  const setError = useCallback((err: unknown) => {
    if (mountedRef.current) {
      const errorResponse = extractErrorResponse(err);
      setState('error');
      setErrorState(errorResponse);
      onError?.(errorResponse);
    }
  }, [onError]);

  const reset = useCallback(() => {
    setState('idle');
    setErrorState(undefined);
  }, []);

  const execute = useCallback(async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setLoading();
    try {
      const result = await fn();
      setSuccess();
      return result;
    } catch (err) {
      setError(err);
      return undefined;
    }
  }, [setLoading, setSuccess, setError]);

  return {
    state,
    error,
    setLoading,
    setSuccess,
    setError,
    reset,
    isIdle: state === 'idle',
    isLoading: state === 'loading',
    isSuccess: state === 'success',
    isError: state === 'error',
    execute,
  };
}

// Hook for managing multiple loading states (e.g., different operations)
interface LoadingStates {
  [key: string]: LoadingStateInfo;
}

interface UseMultiLoadingStateReturn {
  states: LoadingStates;
  setLoading: (key: string) => void;
  setSuccess: (key: string) => void;
  setError: (key: string, error: unknown) => void;
  reset: (key: string) => void;
  resetAll: () => void;
  isAnyLoading: boolean;
  getState: (key: string) => LoadingStateInfo;
}

export function useMultiLoadingState(keys: string[] = []): UseMultiLoadingStateReturn {
  const initialStates: LoadingStates = {};
  keys.forEach(key => {
    initialStates[key] = { state: 'idle' };
  });

  const [states, setStates] = useState<LoadingStates>(initialStates);

  const setLoading = useCallback((key: string) => {
    setStates(prev => ({
      ...prev,
      [key]: { state: 'loading' },
    }));
  }, []);

  const setSuccess = useCallback((key: string) => {
    setStates(prev => ({
      ...prev,
      [key]: { state: 'success' },
    }));
  }, []);

  const setError = useCallback((key: string, error: unknown) => {
    setStates(prev => ({
      ...prev,
      [key]: { state: 'error', error: extractErrorResponse(error) },
    }));
  }, []);

  const reset = useCallback((key: string) => {
    setStates(prev => ({
      ...prev,
      [key]: { state: 'idle' },
    }));
  }, []);

  const resetAll = useCallback(() => {
    setStates(prev => {
      const newStates: LoadingStates = {};
      Object.keys(prev).forEach(key => {
        newStates[key] = { state: 'idle' };
      });
      return newStates;
    });
  }, []);

  const isAnyLoading = Object.values(states).some(s => s.state === 'loading');

  const getState = useCallback((key: string): LoadingStateInfo => {
    return states[key] || { state: 'idle' };
  }, [states]);

  return {
    states,
    setLoading,
    setSuccess,
    setError,
    reset,
    resetAll,
    isAnyLoading,
    getState,
  };
}

export default useLoadingState;
