'use client';

import { Check,FileSpreadsheet, UploadCloud } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';

import { getPresignedUploadUrlApiV1S3PresignedUploadUrlPost } from '@/client/sdk.gen';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import logger from '@/lib/logger';

interface CsvUploadSelectorProps {
  onFileUploaded: (fileKey: string, fileName: string) => void;
  selectedFileName?: string;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
const ALLOWED_EXTENSIONS = ['.csv', '.xlsx', '.xls'];

export default function CsvUploadSelector({ onFileUploaded, selectedFileName }: CsvUploadSelectorProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragActive, setIsDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const getContentType = (fileName: string): string => {
    const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
    if (ext === '.xlsx') return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    if (ext === '.xls') return 'application/vnd.ms-excel';
    return 'text/csv';
  };

  const uploadFile = useCallback(async (file: File) => {
    // Validate file type
    const fileExtension = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(fileExtension)) {
      toast.error('Please select a CSV or Excel (.xlsx, .xls) file');
      return;
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 10MB');
      return;
    }

    setUploading(true);
    setUploadProgress(0);

    const contentType = getContentType(file.name);

    try {
      // Step 1: Request presigned upload URL
      logger.info('Requesting presigned upload URL for:', file.name, 'with content_type:', contentType);
      const { data: presignedData, error } = await getPresignedUploadUrlApiV1S3PresignedUploadUrlPost({
        body: {
          file_name: file.name,
          file_size: file.size,
          content_type: contentType,
        },
      });

      if (error || !presignedData) {
        throw new Error('Failed to get upload URL');
      }

      logger.info('Received presigned URL, uploading file...');

      // Step 2: Upload file directly to S3/MinIO
      const uploadResponse = await fetch(presignedData.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': contentType,
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
      logger.error('Error uploading file:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload file');
    } finally {
      setUploading(false);
      setUploadProgress(0);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, [onFileUploaded]);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    await uploadFile(file);
  };

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setIsDragActive(true);
    } else if (e.type === 'dragleave') {
      setIsDragActive(false);
    }
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      await uploadFile(e.dataTransfer.files[0]);
    }
  }, [uploadFile]);

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="space-y-2">
      <Label>Source File</Label>
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        onChange={handleFileSelect}
        className="hidden"
        disabled={uploading}
      />

      <div
        onDragEnter={handleDrag}
        onDragOver={handleDrag}
        onDragLeave={handleDrag}
        onDrop={handleDrop}
        onClick={handleButtonClick}
        className={`flex flex-col items-center justify-center border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition-all duration-200 min-h-[160px] ${
          isDragActive
            ? 'border-primary bg-primary/5 scale-[1.01]'
            : 'border-border/80 hover:border-primary/50 bg-muted/5 hover:bg-muted/10'
        } ${uploading ? 'pointer-events-none opacity-80' : ''}`}
      >
        {uploading ? (
          <div className="w-full max-w-xs space-y-4">
            <UploadCloud className="h-10 w-10 text-primary animate-pulse mx-auto" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">Uploading your file...</p>
              <Progress value={uploadProgress} className="h-2 w-full" />
              <p className="text-xs text-muted-foreground">{uploadProgress}% uploaded</p>
            </div>
          </div>
        ) : selectedFileName ? (
          <div className="flex flex-col items-center space-y-2 animate-in fade-in zoom-in-95 duration-200">
            <div className="rounded-full bg-emerald-500/10 p-3 text-emerald-500">
              <Check className="h-6 w-6" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">File uploaded successfully</p>
              <p className="text-xs text-muted-foreground mt-1 flex items-center justify-center gap-1 font-mono">
                <FileSpreadsheet className="h-3 w-3 text-primary" />
                {selectedFileName}
              </p>
            </div>
            <span className="text-xs text-primary underline mt-2">Click or drop to replace file</span>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
              <UploadCloud className="h-6 w-6" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                Drag & drop your file here, or <span className="text-primary underline">browse</span>
              </p>
              <p className="text-xs text-muted-foreground">
                Supports CSV, XLSX, XLS (Max 10MB)
              </p>
            </div>
          </div>
        )}
      </div>

      <p className="text-sm text-muted-foreground">
        Upload a file with contact data. Must include a phone number column.
        The columns will be mapped to the workflow fields in the next step.
      </p>
    </div>
  );
}
