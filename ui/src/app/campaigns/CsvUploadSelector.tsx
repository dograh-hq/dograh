'use client';

import { useTranslations } from 'next-intl';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { getPresignedUploadUrlApiV1S3PresignedUploadUrlPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import logger from '@/lib/logger';

interface CsvUploadSelectorProps {
  onFileUploaded: (fileKey: string, fileName: string) => void;
  selectedFileName?: string;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export default function CsvUploadSelector({ onFileUploaded, selectedFileName }: CsvUploadSelectorProps) {
  const t = useTranslations('csvUpload');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.csv')) {
      toast.error(t('selectCsv'));
      return;
    }

    if (file.size > MAX_FILE_SIZE) {
      toast.error(t('fileTooLarge'));
      return;
    }

    setUploading(true);
    setUploadProgress(0);

    try {
      logger.info('Requesting presigned upload URL for:', file.name);
      const { data: presignedData, error } = await getPresignedUploadUrlApiV1S3PresignedUploadUrlPost({
        body: {
          file_name: file.name,
          file_size: file.size,
          content_type: 'text/csv',
        },
      });

      if (error || !presignedData) {
        throw new Error(t('uploadUrlFailed'));
      }

      logger.info('Received presigned URL, uploading file...');

      const uploadResponse = await fetch(presignedData.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': 'text/csv',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error(t('uploadStorageFailed'));
      }

      setUploadProgress(100);
      logger.info('File uploaded successfully, file_key:', presignedData.file_key);

      onFileUploaded(presignedData.file_key, file.name);
      toast.success(t('uploaded', { name: file.name }));
    } catch (error) {
      logger.error('Error uploading CSV:', error);
      toast.error(error instanceof Error ? error.message : t('uploadFailed'));
    } finally {
      setUploading(false);
      setUploadProgress(0);
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
      <Label>{t('csvFile')}</Label>
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
          {uploading ? t('uploading', { progress: uploadProgress }) : t('uploadButton')}
        </Button>
        {selectedFileName && !uploading && (
          <div className="flex-1 text-sm">
            <span className="text-muted-foreground">{t('selected')}</span>
            <span className="text-primary">{selectedFileName}</span>
          </div>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        {t('hint')} <br/>
        {t('maxSize')}
      </p>
    </div>
  );
}
