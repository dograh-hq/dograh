'use client';

import { useTranslations } from 'next-intl';
import { FileText, Info, Upload, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
import type { DocumentUploadResponseSchema } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { useAppConfig } from '@/context/AppConfigContext';
import logger from '@/lib/logger';

interface DocumentUploadProps {
  onUploadSuccess: () => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPTED_FILE_TYPES = ['.pdf', '.docx', '.doc', '.txt', '.json'];

export default function DocumentUpload({ onUploadSuccess }: DocumentUploadProps) {
  const t = useTranslations('documentUpload');
  const { config } = useAppConfig();
  const isOSS = config?.deploymentMode === 'oss';
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [retrievalMode, setRetrievalMode] = useState<string>('full_document');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ossNotice = isOSS ? (
    <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/50 dark:bg-amber-950/30">
      <Info className="h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
      <div className="text-xs text-amber-900 dark:text-amber-200">
        <p className="font-medium">{t('processedExternally')}</p>
        <p className="mt-1">{t('ossNotice')}</p>
      </div>
    </div>
  ) : null;

  const validateFile = (file: File): boolean => {
    const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_TYPES.includes(fileExtension)) {
      toast.error(t('invalidType', { formats: ACCEPTED_FILE_TYPES.join(', ') }));
      return false;
    }

    if (file.size > MAX_FILE_SIZE) {
      toast.error(t('fileTooLarge'));
      return false;
    }

    return true;
  };

  const handleFileSelected = (file: File) => {
    if (!validateFile(file)) {
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }
    setSelectedFile(file);
  };

  const clearSelectedFile = () => {
    setSelectedFile(null);
    setRetrievalMode('full_document');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const uploadFile = async () => {
    if (!selectedFile) return;

    setUploading(true);
    setUploadProgress(0);

    try {
      logger.info('Requesting presigned upload URL for:', selectedFile.name);
      const uploadUrlResponse = await getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
        body: {
          filename: selectedFile.name,
          mime_type: selectedFile.type || 'application/octet-stream',
          custom_metadata: {
            original_filename: selectedFile.name,
            uploaded_at: new Date().toISOString(),
          },
        },
      });

      if (uploadUrlResponse.error || !uploadUrlResponse.data) {
        throw new Error(t('uploadUrlFailed'));
      }

      const uploadData: DocumentUploadResponseSchema = uploadUrlResponse.data;
      setUploadProgress(25);

      const uploadResponse = await fetch(uploadData.upload_url, {
        method: 'PUT',
        body: selectedFile,
        headers: {
          'Content-Type': selectedFile.type || 'application/octet-stream',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error(t('uploadStorageFailed'));
      }

      setUploadProgress(75);

      const processResponse = await processDocumentApiV1KnowledgeBaseProcessDocumentPost({
        body: {
          document_uuid: uploadData.document_uuid,
          s3_key: uploadData.s3_key,
          retrieval_mode: retrievalMode,
        },
      });

      if (processResponse.error) {
        throw new Error(t('processingFailed'));
      }

      setUploadProgress(100);
      toast.success(t('uploaded', { name: selectedFile.name }));
      clearSelectedFile();
      onUploadSuccess();
    } catch (error) {
      logger.error('Error uploading document:', error);
      toast.error(error instanceof Error ? error.message : t('uploadFailed'));
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      handleFileSelected(file);
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

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const file = e.dataTransfer.files?.[0];
    if (file) {
      handleFileSelected(file);
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  // Step 2: File selected — show retrieval mode choice
  if (selectedFile && !uploading) {
    return (
      <div className="space-y-4">
        {ossNotice}
        {/* Selected file info */}
        <div className="flex items-center gap-3 p-3 border rounded-lg bg-muted/30">
          <FileText className="w-8 h-8 text-primary flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium truncate">{selectedFile.name}</p>
            <p className="text-xs text-muted-foreground">
              {(selectedFile.size / 1024).toFixed(1)} KB
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={clearSelectedFile}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Retrieval mode selection */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">{t('selectFileType')}</Label>
          <RadioGroup value={retrievalMode} onValueChange={setRetrievalMode}>
            <label
              htmlFor="full_document"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'full_document' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="full_document" id="full_document" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">{t('fullDocument')}</p>
                <p className="text-xs text-muted-foreground">{t('fullDocumentDesc')}</p>
              </div>
            </label>
            <label
              htmlFor="chunked"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'chunked' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="chunked" id="chunked" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">{t('chunkedSearch')}</p>
                <p className="text-xs text-muted-foreground">{t('chunkedSearchDesc')}</p>
              </div>
            </label>
          </RadioGroup>
        </div>

        {/* Upload button */}
        <Button onClick={uploadFile} className="w-full">
          {t('uploadProcess')}
        </Button>
      </div>
    );
  }

  const formatsStr = ACCEPTED_FILE_TYPES.join(', ');

  return (
    <div className="space-y-4">
      {ossNotice}
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
          {uploading ? t('uploading') : t('dropHere')}
        </p>
        <p className="text-sm text-muted-foreground mb-4">
          {t('clickToBrowse')}
        </p>
        <p className="text-xs text-muted-foreground">
          {t('supportedFormats', { formats: formatsStr })}
        </p>
      </div>

      {/* Upload Progress */}
      {uploading && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>{t('uploading')}</span>
            <span>{uploadProgress}%</span>
          </div>
          <Progress value={uploadProgress} />
        </div>
      )}

      {/* Manual Upload Button */}
      {!uploading && (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="outline"
            onClick={handleButtonClick}
          >
            {t('chooseFile')}
          </Button>
        </div>
      )}
    </div>
  );
}
