'use client';

import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import logger from '@/lib/logger';

interface CsvUploadSelectorProps {
  accessToken: string;
  onFileUploaded: (fileKey: string, fileName: string) => void;
  selectedFileName?: string;
}

interface PresignedUploadUrlResponse {
  upload_url: string;
  file_key: string;
  expires_in: number;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export default function CsvUploadSelector({ accessToken, onFileUploaded, selectedFileName }: CsvUploadSelectorProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.name.endsWith('.csv')) {
      toast.error('Please select a CSV file');
      return;
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 10MB');
      return;
    }

    setUploading(true);
    setUploadProgress(0);

    try {
      // Step 1: Request presigned upload URL
      logger.info('Requesting presigned upload URL for:', file.name);
      const presignedResponse = await fetch('/api/v1/s3/presigned-upload-url', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          file_name: file.name,
          file_size: file.size,
          content_type: 'text/csv',
        }),
      });

      if (!presignedResponse.ok) {
        const error = await presignedResponse.json();
        throw new Error(error.detail || 'Failed to get upload URL');
      }

      const presignedData: PresignedUploadUrlResponse = await presignedResponse.json();
      logger.info('Received presigned URL, uploading file...');

      // Step 2: Upload file directly to S3/MinIO
      const uploadResponse = await fetch(presignedData.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': 'text/csv',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error('Failed to upload file to storage');
      }

      setUploadProgress(100);
      logger.info('File uploaded successfully, file_key:', presignedData.file_key);

      // Step 3: Notify parent with file_key
      onFileUploaded(presignedData.file_key, file.name);
      toast.success(`File uploaded: ${file.name}`);
    } catch (error) {
      logger.error('Error uploading CSV:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload CSV file');
    } finally {
      setUploading(false);
      setUploadProgress(0);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="space-y-2">
      <Label>CSV File</Label>
      <div className="flex items-center gap-4">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          onChange={handleFileSelect}
          className="hidden"
        />
        <Button
          type="button"
          variant="outline"
          onClick={handleButtonClick}
          disabled={uploading}
        >
          {uploading ? `Uploading... ${uploadProgress}%` : 'Upload CSV File'}
        </Button>
        {selectedFileName && !uploading && (
          <div className="flex-1 text-sm">
            <span className="text-gray-600">Selected: </span>
            <span className="text-blue-600">{selectedFileName}</span>
          </div>
        )}
      </div>
      <p className="text-sm text-gray-500">
        Upload a CSV file with contact data. Must include phone_number, first_name, and last_name columns. Max 10MB.
      </p>
    </div>
  );
}
