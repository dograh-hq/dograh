'use client';

import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { getIntegrationAccessTokenApiV1IntegrationIntegrationIdAccessTokenGet, getIntegrationsApiV1IntegrationGet } from '@/client/sdk.gen';
import type { IntegrationResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import logger from '@/lib/logger';

interface GoogleSheetSelectorProps {
  accessToken: string;
  onSheetSelected: (sheetUrl: string, sheetName: string) => void;
  selectedSheetUrl?: string;
}

interface PickerBuilder {
  addView: (viewId: string) => PickerBuilder;
  setOAuthToken: (token: string) => PickerBuilder;
  setDeveloperKey: (key: string) => PickerBuilder;
  setCallback: (callback: (data: { action: string; docs?: Array<{ id: string; name: string; url: string }> }) => void) => PickerBuilder;
  setTitle: (title: string) => PickerBuilder;
  build: () => { setVisible: (visible: boolean) => void };
}

declare global {
  interface Window {
    gapi: {
      load: (library: string, callback: () => void) => void;
    };
    google: {
      picker: {
        PickerBuilder: new () => PickerBuilder;
        ViewId: {
          SPREADSHEETS: string;
        };
        Action: {
          PICKED: string;
        };
      };
    };
  }
}

// Google API configuration
const GOOGLE_API_KEY = process.env.NEXT_PUBLIC_GOOGLE_API_KEY || '';

export default function GoogleSheetSelector({ accessToken, onSheetSelected, selectedSheetUrl }: GoogleSheetSelectorProps) {
  const [loading, setLoading] = useState(false);
  const [pickerApiLoaded, setPickerApiLoaded] = useState(false);
  const [googleIntegration, setGoogleIntegration] = useState<IntegrationResponse | null>(null);
  const [selectedSheetName, setSelectedSheetName] = useState<string>('');
  const [checkingIntegration, setCheckingIntegration] = useState(true);

  // Load Google Picker API
  useEffect(() => {
    const script = document.createElement('script');
    script.src = 'https://apis.google.com/js/api.js';
    script.onload = () => {
      window.gapi.load('picker', () => {
        setPickerApiLoaded(true);
        logger.info('Google Picker API loaded');
      });
    };
    document.body.appendChild(script);

    return () => {
      if (document.body.contains(script)) {
        document.body.removeChild(script);
      }
    };
  }, []);

  // Check for Google Sheet integration
  useEffect(() => {
    const checkGoogleIntegration = async () => {
      if (!accessToken) {
        return;
      }

      try {
        const response = await getIntegrationsApiV1IntegrationGet({
          headers: {
            'Authorization': `Bearer ${accessToken}`,
          }
        });

        if (response.data) {
          const integrations = Array.isArray(response.data) ? response.data : [response.data];
          const googleSheet = integrations.find(i => i.provider === 'google-sheet');
          setGoogleIntegration(googleSheet || null);
        }
      } catch (error) {
        logger.error('Failed to check Google integration:', error);
      } finally {
        setCheckingIntegration(false);
      }
    };

    checkGoogleIntegration();
  }, [accessToken]);

  const fetchGoogleAccessToken = async () => {
    if (!googleIntegration) return null;

    try {
      const response = await getIntegrationAccessTokenApiV1IntegrationIntegrationIdAccessTokenGet({
        path: {
          integration_id: googleIntegration.id,
        },
        headers: {
          Authorization: `Bearer ${accessToken}`,
        }
      });

      if (response.data?.access_token) {
        return response.data.access_token;
      }
      return null;
    } catch (error) {
      logger.error('Failed to fetch Google access token:', error);
      return null;
    }
  };

  const openGooglePicker = async () => {
    if (!pickerApiLoaded) {
      toast.error('Google Picker is still loading. Please try again.');
      return;
    }

    if (!GOOGLE_API_KEY) {
      toast.error('Google API Key is not configured.');
      return;
    }

    if (!googleIntegration) {
      toast.error('Please connect Google Sheets in the Integrations page first.');
      return;
    }

    setLoading(true);

    try {
      const token = await fetchGoogleAccessToken();
      if (!token) {
        toast.error('Failed to get Google access token. Please re-authorize in Integrations.');
        setLoading(false);
        return;
      }

      const picker = new window.google.picker.PickerBuilder()
        .addView(window.google.picker.ViewId.SPREADSHEETS)
        .setOAuthToken(token)
        .setDeveloperKey(GOOGLE_API_KEY)
        .setCallback((data: { action: string; docs?: Array<{ id: string; name: string; url: string }> }) => {
          if (data.action === window.google.picker.Action.PICKED && data.docs && data.docs.length > 0) {
            const doc = data.docs[0];
            setSelectedSheetName(doc.name);
            onSheetSelected(doc.url, doc.name);
            toast.success(`Selected: ${doc.name}`);
          }
          setLoading(false);
        })
        .setTitle('Select a Google Sheet for your campaign')
        .build();

      picker.setVisible(true);
    } catch (error) {
      toast.error('Error opening Google Picker');
      logger.error('Error opening Google Picker:', error);
      setLoading(false);
    }
  };

  if (checkingIntegration) {
    return (
      <div className="space-y-2">
        <Label>Google Sheet</Label>
        <div className="text-sm text-muted-foreground">Checking Google integration...</div>
      </div>
    );
  }

  if (!googleIntegration) {
    return (
      <div className="space-y-2">
        <Label>Google Sheet</Label>
        <div className="p-4 border border-amber-200 bg-amber-50 rounded-md">
          <p className="text-sm text-amber-800 mb-2">
            Google Sheets integration not found
          </p>
          <p className="text-sm text-amber-700">
            Please go to the{' '}
            <a href="/integrations" className="text-amber-900 underline font-medium">
              Integrations page
            </a>
            {' '}and connect your Google account first.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Label>Google Sheet</Label>
      <div className="flex items-center gap-4">
        <Button
          type="button"
          variant="outline"
          onClick={openGooglePicker}
          disabled={loading}
        >
          {loading ? 'Opening...' : 'Select Google Sheet'}
        </Button>
        {selectedSheetUrl && (
          <div className="flex-1 text-sm">
            <span className="text-muted-foreground">Selected: </span>
            <a
              href={selectedSheetUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline"
            >
              {selectedSheetName || selectedSheetUrl}
            </a>
          </div>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        Select a Google Sheet from your connected Google account
      </p>
    </div>
  );
}
