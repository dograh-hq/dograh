'use client';

import { Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
import type { DocumentUploadResponseSchema } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import logger from '@/lib/logger';

interface DocumentUploadProps {
  accessToken: string;
  onUploadSuccess: () => void;
}

const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB
const ACCEPTED_FILE_TYPES = ['.pdf', '.docx', '.doc', '.txt'];

export default function DocumentUpload({ accessToken, onUploadSuccess }: DocumentUploadProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateFile = (file: File): boolean => {
    // Validate file type
    const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_TYPES.includes(fileExtension)) {
      toast.error(`Please select a supported file type: ${ACCEPTED_FILE_TYPES.join(', ')}`);
      return false;
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 100MB');
      return false;
    }

    return true;
  };

  const uploadFile = async (file: File) => {
    if (!validateFile(file)) return;

    setUploading(true);
    setUploadProgress(0);

    try {
      // Step 1: Request presigned upload URL
      logger.info('Requesting presigned upload URL for:', file.name);
      const uploadUrlResponse = await getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
        body: {
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          custom_metadata: {
            original_filename: file.name,
            uploaded_at: new Date().toISOString(),
          },
        },
        headers: {
          'Authorization': `Bearer ${accessToken}`,
        },
      });

      if (uploadUrlResponse.error || !uploadUrlResponse.data) {
        throw new Error('Failed to get upload URL');
      }

      const uploadData: DocumentUploadResponseSchema = uploadUrlResponse.data;
      logger.info('Received presigned URL, uploading file...');

      setUploadProgress(25);

      // Step 2: Upload file directly to S3/MinIO using PUT
      const uploadResponse = await fetch(uploadData.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': file.type || 'application/octet-stream',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error('Failed to upload file to storage');
      }

      setUploadProgress(75);
      logger.info('File uploaded successfully, triggering processing...');

      // Step 3: Trigger document processing
      const processResponse = await processDocumentApiV1KnowledgeBaseProcessDocumentPost({
        body: {
          document_uuid: uploadData.document_uuid,
          s3_key: uploadData.s3_key,
        },
        headers: {
          'Authorization': `Bearer ${accessToken}`,
        },
      });

      if (processResponse.error) {
        throw new Error('Failed to trigger processing');
      }

      setUploadProgress(100);
      logger.info('Document processing triggered successfully');

      toast.success(`File uploaded: ${file.name}. Processing started.`);
      onUploadSuccess();
    } catch (error) {
      logger.error('Error uploading document:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload document');
    } finally {
      setUploading(false);
      setUploadProgress(0);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      await uploadFile(file);
    }
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const file = e.dataTransfer.files?.[0];
    if (file) {
      await uploadFile(file);
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="space-y-4">
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_FILE_TYPES.join(',')}
        onChange={handleFileSelect}
        className="hidden"
        disabled={uploading}
      />

      {/* Drag and Drop Area */}
      <div
        className={`
          border-2 border-dashed rounded-lg p-8 text-center transition-colors
          ${dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/25'}
          ${uploading ? 'opacity-50 pointer-events-none' : 'cursor-pointer hover:border-primary hover:bg-muted/50'}
        `}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={handleButtonClick}
      >
        <Upload className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <p className="text-lg font-medium mb-2">
          {uploading ? 'Uploading...' : 'Drop your document here'}
        </p>
        <p className="text-sm text-muted-foreground mb-4">
          or click to browse
        </p>
        <p className="text-xs text-muted-foreground">
          Supported formats: {ACCEPTED_FILE_TYPES.join(', ')} (Max 100MB)
        </p>
      </div>

      {/* Upload Progress */}
      {uploading && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>Uploading...</span>
            <span>{uploadProgress}%</span>
          </div>
          <Progress value={uploadProgress} />
        </div>
      )}

      {/* Manual Upload Button */}
      <div className="flex justify-center">
        <Button
          type="button"
          variant="outline"
          onClick={handleButtonClick}
          disabled={uploading}
        >
          {uploading ? 'Uploading...' : 'Choose File'}
        </Button>
      </div>
    </div>
  );
}
