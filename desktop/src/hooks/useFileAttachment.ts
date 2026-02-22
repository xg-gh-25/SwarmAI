import { useState, useCallback } from 'react';
import type { FileAttachment, AttachmentType } from '../types';
import { FILE_SIZE_LIMITS, MAX_ATTACHMENTS, SUPPORTED_FILE_TYPES } from '../types';

/**
 * Generate a unique ID for attachments
 */
const generateId = (): string => {
  return `attachment_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
};

/**
 * Determine the attachment type from MIME type
 */
const getAttachmentType = (mimeType: string): AttachmentType | null => {
  if ((SUPPORTED_FILE_TYPES.image as readonly string[]).includes(mimeType)) return 'image';
  if ((SUPPORTED_FILE_TYPES.pdf as readonly string[]).includes(mimeType)) return 'pdf';
  if ((SUPPORTED_FILE_TYPES.text as readonly string[]).includes(mimeType)) return 'text';
  if ((SUPPORTED_FILE_TYPES.csv as readonly string[]).includes(mimeType)) return 'csv';
  return null;
};

/**
 * Validate file size based on type
 */
const validateFileSize = (file: File, type: AttachmentType): string | null => {
  const limit = FILE_SIZE_LIMITS[type];
  if (file.size > limit) {
    const limitMB = limit / (1024 * 1024);
    return `File too large. Max size for ${type}: ${limitMB}MB`;
  }
  return null;
};

/**
 * Read file as base64 data URL
 */
const readFileAsBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Remove the data URL prefix (e.g., "data:image/png;base64,")
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
};

/**
 * Read file as data URL (for preview)
 */
const readFileAsDataURL = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('Failed to read file for preview'));
    reader.readAsDataURL(file);
  });
};

/**
 * Read text file content for preview
 */
const readTextFilePreview = (file: File, maxChars: number = 200): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result as string;
      if (text.length > maxChars) {
        resolve(text.substring(0, maxChars) + '...');
      } else {
        resolve(text);
      }
    };
    reader.onerror = () => reject(new Error('Failed to read text file'));
    reader.readAsText(file);
  });
};

export interface UseFileAttachmentReturn {
  attachments: FileAttachment[];
  addFiles: (files: File[]) => Promise<void>;
  removeFile: (id: string) => void;
  clearAll: () => void;
  isProcessing: boolean;
  error: string | null;
  canAddMore: boolean;
}

/**
 * Hook for managing file attachments in chat
 */
export function useFileAttachment(): UseFileAttachmentReturn {
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canAddMore = attachments.length < MAX_ATTACHMENTS;

  /**
   * Add files to the attachment list
   */
  const addFiles = useCallback(async (files: File[]) => {
    setError(null);

    // Check max attachments limit
    const remainingSlots = MAX_ATTACHMENTS - attachments.length;
    if (remainingSlots <= 0) {
      setError(`Maximum ${MAX_ATTACHMENTS} attachments allowed`);
      return;
    }

    // Limit to remaining slots
    const filesToProcess = files.slice(0, remainingSlots);

    setIsProcessing(true);

    try {
      const newAttachments: FileAttachment[] = [];

      for (const file of filesToProcess) {
        const id = generateId();

        // Determine type
        const type = getAttachmentType(file.type);
        if (!type) {
          setError(`Unsupported file type: ${file.type || file.name}`);
          continue;
        }

        // Validate size
        const sizeError = validateFileSize(file, type);
        if (sizeError) {
          setError(sizeError);
          continue;
        }

        // Create initial attachment object (loading state)
        const attachment: FileAttachment = {
          id,
          file,
          name: file.name,
          type,
          size: file.size,
          mediaType: file.type,
          isLoading: true,
        };

        newAttachments.push(attachment);
      }

      // Add all valid attachments immediately (in loading state)
      if (newAttachments.length > 0) {
        setAttachments((prev) => [...prev, ...newAttachments]);
      }

      // Process each attachment asynchronously
      for (const attachment of newAttachments) {
        try {
          // Read base64 content
          const base64 = await readFileAsBase64(attachment.file);

          // Generate preview based on type
          let preview: string | undefined;
          if (attachment.type === 'image') {
            preview = await readFileAsDataURL(attachment.file);
          } else if (attachment.type === 'text' || attachment.type === 'csv') {
            preview = await readTextFilePreview(attachment.file);
          }

          // Update attachment with base64 and preview
          setAttachments((prev) =>
            prev.map((a) =>
              a.id === attachment.id
                ? { ...a, base64, preview, isLoading: false }
                : a
            )
          );
        } catch {
          // Update attachment with error
          setAttachments((prev) =>
            prev.map((a) =>
              a.id === attachment.id
                ? { ...a, error: 'Failed to process file', isLoading: false }
                : a
            )
          );
        }
      }
    } finally {
      setIsProcessing(false);
    }
  }, [attachments.length]);

  /**
   * Remove a file from the attachment list
   */
  const removeFile = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    setError(null);
  }, []);

  /**
   * Clear all attachments
   */
  const clearAll = useCallback(() => {
    setAttachments([]);
    setError(null);
  }, []);

  return {
    attachments,
    addFiles,
    removeFile,
    clearAll,
    isProcessing,
    error,
    canAddMore,
  };
}
