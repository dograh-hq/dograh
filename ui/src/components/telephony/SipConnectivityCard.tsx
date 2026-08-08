"use client";

import { ChevronDown, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type {
  SipConnectivityDetails,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";
import { CloudonixOutboundTrunkForm } from "@/components/telephony/CloudonixOutboundTrunkForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { copyTextToClipboard } from "@/lib/clipboard";

interface SipConnectivityCardProps {
  details: SipConnectivityDetails;
  configuration: TelephonyConfigurationDetail;
  onSaved: (
    configuration: TelephonyConfigurationDetail,
  ) => void | Promise<void>;
}

export function SipConnectivityCard({
  details,
  configuration,
  onSaved,
}: SipConnectivityCardProps) {
  const [open, setOpen] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState<string>();
  const defaultRegion =
    details.regions.find(
      (candidate) => candidate.region.toLowerCase() === "global",
    ) ?? details.regions[0];
  const region =
    details.regions.find((candidate) => candidate.region === selectedRegion) ??
    defaultRegion;

  if (!region) return null;

  const copyValue = (value: string, label: string) => {
    copyTextToClipboard(value)
      .then(() => toast.success(`${label} copied`))
      .catch(() => toast.error(`Failed to copy ${label.toLowerCase()}`));
  };

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle>SIP connectivity</CardTitle>
            <CardDescription>
              Configure how SIP calls enter and leave Dograh through this provider.
            </CardDescription>
          </div>
          <CollapsibleTrigger asChild>
            <Button variant="outline" size="sm" className="shrink-0">
              {open ? "Hide details" : "View details"}
              <ChevronDown
                className={`ml-2 h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
              />
            </Button>
          </CollapsibleTrigger>
        </CardHeader>

        <CollapsibleContent>
          <CardContent className="space-y-6 border-t pt-6">
            <div className="max-w-xs space-y-2">
              <p className="text-sm font-medium">Select Region</p>
              <Select value={region.region} onValueChange={setSelectedRegion}>
                <SelectTrigger aria-label="SIP region">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {details.regions.map((candidate) => (
                    <SelectItem key={candidate.region} value={candidate.region}>
                      {candidate.region}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <section className="overflow-hidden rounded-md border">
              <div className="border-b bg-muted/20 p-4">
                <h3 className="font-semibold">Inbound</h3>
                <p className="text-sm text-muted-foreground">
                  Route calls to {details.provider_display_name}/Dograh using one of
                  these SIP endpoints.
                </p>
              </div>
              <div className="overflow-x-auto p-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Transport</TableHead>
                      <TableHead>Hostname</TableHead>
                      <TableHead>Port</TableHead>
                      <TableHead>URI</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {region.inbound_transports.map((transport) => (
                      <TableRow key={transport.transport}>
                        <TableCell>
                          <Badge variant="outline">{transport.transport}</Badge>
                        </TableCell>
                        <TableCell className="whitespace-nowrap font-mono text-xs">
                          {transport.hostname}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {transport.port}
                        </TableCell>
                        <TableCell>
                          <button
                            type="button"
                            onClick={() => copyValue(transport.uri, "SIP URI")}
                            title={`Copy ${transport.transport} SIP URI`}
                            aria-label={`Copy ${transport.transport} SIP URI`}
                            className="inline-flex items-center gap-2 whitespace-nowrap rounded font-mono text-xs hover:text-foreground"
                          >
                            {transport.uri}
                            <Copy className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          </button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>

            <section className="overflow-hidden rounded-md border">
              <div className="border-b bg-muted/20 p-4">
                <h3 className="font-semibold">Outbound</h3>
                <p className="text-sm text-muted-foreground">
                  Send calls from {details.provider_display_name}/Dograh to your SIP
                  carrier or PBX.
                </p>
              </div>
              <div className="space-y-5 p-4">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-muted/30 px-3 py-2 text-sm">
                  <span className="text-muted-foreground">Origin IP address</span>
                  <button
                    type="button"
                    onClick={() =>
                      copyValue(region.outbound_origin_ip, "Origin IP address")
                    }
                    title="Copy outbound origin IP address"
                    aria-label="Copy outbound origin IP address"
                    className="inline-flex items-center gap-2 rounded font-mono hover:text-foreground"
                  >
                    {region.outbound_origin_ip}
                    <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                  </button>
                </div>

                {configuration.provider === "cloudonix" ? (
                  <CloudonixOutboundTrunkForm
                    configuration={configuration}
                    onSaved={onSaved}
                  />
                ) : null}
              </div>
            </section>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}
