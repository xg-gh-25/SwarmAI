import axios from 'axios';
import type { AxiosError, AxiosResponse } from 'axios';
import type { ErrorResponse, RateLimitErrorResponse } from '../types';
import { ErrorCodes } from '../types';
import { getBackendPort } from './tauri';

// Create axios instance with base configuration
// For desktop app, we connect directly to the local backend
const api = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
});

// Dynamic base URL based on backend port
api.interceptors.request.use((config) => {
  const port = getBackendPort();
  config.baseURL = `http://localhost:${port}/api`;
  return config;
});

// Error parsing utilities
export function isErrorResponse(data: unknown): data is ErrorResponse {
  return (
    typeof data === 'object' &&
    data !== null &&
    'code' in data &&
    'message' in data &&
    typeof (data as ErrorResponse).code === 'string' &&
    typeof (data as ErrorResponse).message === 'string'
  );
}

export function parseApiError(error: AxiosError): ErrorResponse {
  // Handle network errors
  if (!error.response) {
    return {
      code: ErrorCodes.SERVICE_UNAVAILABLE,
      message: 'Unable to connect to backend service',
      detail: error.message || 'Network error occurred',
      suggestedAction: 'Please check that the backend service is running',
    };
  }

  const { status, data } = error.response;

  // If server returned structured error, use it
  if (isErrorResponse(data)) {
    return data;
  }

  // Otherwise, create error from status code
  switch (status) {
    case 400:
      return {
        code: ErrorCodes.VALIDATION_FAILED,
        message: 'Invalid request',
        detail: typeof data === 'string' ? data : JSON.stringify(data),
        suggestedAction: 'Please check your input and try again',
      };
    case 404:
      return {
        code: 'NOT_FOUND',
        message: 'Resource not found',
        suggestedAction: 'The requested resource does not exist',
      };
    case 429:
      const retryAfter = error.response.headers['retry-after'];
      return {
        code: ErrorCodes.RATE_LIMIT_EXCEEDED,
        message: 'Too many requests',
        detail: `Please wait ${retryAfter || 60} seconds before trying again`,
        suggestedAction: 'Slow down your requests',
      };
    case 500:
      return {
        code: ErrorCodes.SERVER_ERROR,
        message: 'Server error',
        detail: 'An unexpected error occurred on the server',
        suggestedAction: 'Please try again later',
      };
    case 503:
      return {
        code: ErrorCodes.SERVICE_UNAVAILABLE,
        message: 'Service temporarily unavailable',
        suggestedAction: 'Please try again in a few moments',
      };
    default:
      return {
        code: ErrorCodes.SERVER_ERROR,
        message: `Request failed with status ${status}`,
        suggestedAction: 'Please try again later',
      };
  }
}

// Get user-friendly message for error codes
export function getErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    [ErrorCodes.VALIDATION_FAILED]: 'Please check your input',
    [ErrorCodes.AUTH_TOKEN_MISSING]: 'Please log in to continue',
    [ErrorCodes.AUTH_TOKEN_INVALID]: 'Your session is invalid',
    [ErrorCodes.AUTH_TOKEN_EXPIRED]: 'Your session has expired',
    [ErrorCodes.FORBIDDEN]: 'You do not have permission',
    [ErrorCodes.AGENT_NOT_FOUND]: 'Agent not found',
    [ErrorCodes.SKILL_NOT_FOUND]: 'Skill not found',
    [ErrorCodes.MCP_SERVER_NOT_FOUND]: 'MCP server not found',
    [ErrorCodes.SESSION_NOT_FOUND]: 'Session not found',
    [ErrorCodes.DUPLICATE_RESOURCE]: 'Resource already exists',
    [ErrorCodes.RATE_LIMIT_EXCEEDED]: 'Too many requests',
    [ErrorCodes.SERVER_ERROR]: 'Something went wrong',
    [ErrorCodes.AGENT_EXECUTION_ERROR]: 'Agent execution failed',
    [ErrorCodes.AGENT_TIMEOUT]: 'Agent response timed out',
    [ErrorCodes.SERVICE_UNAVAILABLE]: 'Service unavailable',
    [ErrorCodes.DATABASE_UNAVAILABLE]: 'Database unavailable',
  };
  return messages[code] || 'An error occurred';
}

// Custom error class for API errors
export class ApiError extends Error {
  public readonly response: ErrorResponse;
  public readonly statusCode: number;

  constructor(response: ErrorResponse, statusCode: number = 500) {
    super(response.message);
    this.name = 'ApiError';
    this.response = response;
    this.statusCode = statusCode;
  }

  get code(): string {
    return this.response.code;
  }

  get detail(): string | undefined {
    return this.response.detail;
  }

  get suggestedAction(): string | undefined {
    return this.response.suggestedAction;
  }

  get requestId(): string | undefined {
    return this.response.requestId;
  }

  isNotFoundError(): boolean {
    return this.response.code.endsWith('_NOT_FOUND');
  }

  isRateLimitError(): boolean {
    return this.response.code === ErrorCodes.RATE_LIMIT_EXCEEDED;
  }

  isServerError(): boolean {
    return this.statusCode >= 500;
  }
}

// Response interceptor with enhanced error handling
api.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    const parsedError = parseApiError(error);
    const statusCode = error.response?.status || 500;

    // Handle rate limiting with retry-after header
    if (parsedError.code === ErrorCodes.RATE_LIMIT_EXCEEDED) {
      const retryAfter = error.response?.headers['retry-after'];
      if (retryAfter) {
        (parsedError as RateLimitErrorResponse).retryAfter = parseInt(retryAfter, 10);
      }
    }

    return Promise.reject(new ApiError(parsedError, statusCode));
  }
);

// Helper function to check if an error is an ApiError
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

// Helper function to extract error response from any error
export function extractErrorResponse(error: unknown): ErrorResponse {
  if (isApiError(error)) {
    return error.response;
  }

  if (error instanceof Error) {
    return {
      code: ErrorCodes.SERVER_ERROR,
      message: error.message,
      suggestedAction: 'Please try again later',
    };
  }

  return {
    code: ErrorCodes.SERVER_ERROR,
    message: 'An unexpected error occurred',
    suggestedAction: 'Please try again later',
  };
}

export default api;
