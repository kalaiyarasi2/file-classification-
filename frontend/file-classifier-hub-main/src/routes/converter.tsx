import { createFileRoute } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  RefreshCw, FileText, CheckCircle2, XCircle, Upload, Calendar,
  FileSpreadsheet, FileJson, FileCode, Play, RotateCcw, AlertCircle
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { PdfDropzone } from "@/components/PdfDropzone";
import { api, type ConversionHistoryRecord } from "@/lib/api";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/converter")({ component: ConverterPage });

const SOURCE_FORMATS = [
  { value: "csv", label: "CSV (.csv)", icon: FileSpreadsheet },
  { value: "excel", label: "Excel (.xlsx, .xls)", icon: FileSpreadsheet },
  { value: "json", label: "JSON (.json)", icon: FileJson },
  { value: "xml", label: "XML (.xml)", icon: FileCode },
  { value: "pdf", label: "PDF (.pdf)", icon: FileText },
];

const TARGET_MAPPING: Record<string, { value: string; label: string }[]> = {
  csv: [{ value: "json", label: "JSON" }],
  excel: [{ value: "json", label: "JSON" }],
  json: [
    { value: "excel", label: "Excel (.xlsx)" },
    { value: "xml", label: "XML" }
  ],
  xml: [{ value: "json", label: "JSON" }],
  pdf: [{ value: "txt", label: "Text (.txt)" }],
};

function ConverterPage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [sourceFormat, setSourceFormat] = useState<string>("");
  const [targetFormat, setTargetFormat] = useState<string>("");
  const [converting, setConverting] = useState(false);

  // Fetch conversion history
  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["conversion-history"],
    queryFn: () => api.getConversionHistory(50),
    refetchInterval: 10000,
  });

  // Auto-detect format based on file extension
  useEffect(() => {
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase();
    let detected = "";
    if (ext === "csv") detected = "csv";
    else if (ext === "xlsx" || ext === "xls") detected = "excel";
    else if (ext === "json") detected = "json";
    else if (ext === "xml") detected = "xml";
    else if (ext === "pdf") detected = "pdf";

    if (detected) {
      setSourceFormat(detected);
      const targets = TARGET_MAPPING[detected] || [];
      if (targets.length > 0) {
        setTargetFormat(targets[0].value);
      }
    }
  }, [file]);

  // Handle format change
  const handleSourceChange = (val: string) => {
    setSourceFormat(val);
    const targets = TARGET_MAPPING[val] || [];
    if (targets.length > 0) {
      setTargetFormat(targets[0].value);
    } else {
      setTargetFormat("");
    }
  };

  const handleConvert = async () => {
    if (!file) {
      toast.error("Please upload a file to convert.");
      return;
    }
    if (!sourceFormat || !targetFormat) {
      toast.error("Please select both source and target formats.");
      return;
    }

    setConverting(true);
    const toastId = toast.loading("Converting file... Please wait.");
    try {
      const response = await api.convertFile(file, sourceFormat, targetFormat);
      if (!response.ok) {
        let errorDetail = "Failed to convert file";
        try {
          const errJson = await response.json();
          errorDetail = errJson.detail || JSON.stringify(errJson);
        } catch {}
        throw new Error(errorDetail);
      }

      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = downloadUrl;

      // Extract original file name without extension
      const originalName = file.name.substring(0, file.name.lastIndexOf(".")) || file.name;
      const targetExt = targetFormat === "excel" ? "xlsx" : targetFormat;
      link.setAttribute("download", `${originalName}_converted.${targetExt}`);
      
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(downloadUrl);

      toast.success("File converted and downloaded successfully!", { id: toastId });
      
      // Invalidate history to fetch the new record
      queryClient.invalidateQueries({ queryKey: ["conversion-history"] });
    } catch (error: any) {
      toast.error(error.message || "An error occurred during conversion.", { id: toastId });
      queryClient.invalidateQueries({ queryKey: ["conversion-history"] });
    } finally {
      setConverting(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setSourceFormat("");
    setTargetFormat("");
  };

  return (
    <>
      <PageHeader
        icon={RefreshCw}
        title="Universal File Converter"
        description="Convert files between tabular formats, structured documents, or extract clean text from PDFs."
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Conversion controls */}
        <div className="lg:col-span-2">
          <Panel title="Convert File" description="Upload a file and choose your desired format.">
            <div className="space-y-4">
              <div>
                <Label className="text-[11.5px] font-semibold mb-1 block">Upload File</Label>
                <PdfDropzone
                  file={file}
                  onFiles={(files) => setFile(files[0])}
                  accept=".csv,.xlsx,.xls,.json,.xml,.pdf"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Label className="text-[11.5px] font-semibold mb-1 block">Source Format</Label>
                  <select
                    value={sourceFormat}
                    onChange={(e) => handleSourceChange(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <option value="" disabled>Select source format</option>
                    {SOURCE_FORMATS.map((f) => (
                      <option key={f.value} value={f.value}>{f.label}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <Label className="text-[11.5px] font-semibold mb-1 block">Target Format</Label>
                  <select
                    value={targetFormat}
                    onChange={(e) => setTargetFormat(e.target.value)}
                    disabled={!sourceFormat}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
                  >
                    <option value="" disabled>Select target format</option>
                    {(TARGET_MAPPING[sourceFormat] || []).map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleReset}
                  disabled={converting}
                >
                  <RotateCcw className="w-3.5 h-3.5 mr-1" /> Reset
                </Button>
                <Button
                  size="sm"
                  onClick={handleConvert}
                  disabled={converting || !file || !sourceFormat || !targetFormat}
                  className="bg-primary text-primary-foreground hover:bg-primary/90"
                >
                  {converting ? (
                    <>
                      <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" /> Converting...
                    </>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5 mr-1" /> Convert & Download
                    </>
                  )}
                </Button>
              </div>
            </div>
          </Panel>
        </div>

        {/* Dynamic tips card */}
        <div className="lg:col-span-1">
          <Panel title="Supported Conversions" description="Allowed format routing table.">
            <div className="space-y-3.5 text-[12.5px]">
              <div className="rounded-md bg-muted/40 p-3 border border-border">
                <h3 className="font-semibold text-primary mb-1">CSV ➡️ JSON</h3>
                <p className="text-muted-foreground text-[11px] leading-normal">
                  Parses tabular CSV rows into a JSON array of structured objects.
                </p>
              </div>
              <div className="rounded-md bg-muted/40 p-3 border border-border">
                <h3 className="font-semibold text-primary mb-1">Excel ➡️ JSON</h3>
                <p className="text-muted-foreground text-[11px] leading-normal">
                  Reads sheet tables and converts rows into formatted JSON format.
                </p>
              </div>
              <div className="rounded-md bg-muted/40 p-3 border border-border">
                <h3 className="font-semibold text-primary mb-1">JSON ➡️ Excel / XML</h3>
                <p className="text-muted-foreground text-[11px] leading-normal">
                  Outputs arrays to Excel worksheets or serializes structured JSON keys into XML tags.
                </p>
              </div>
              <div className="rounded-md bg-muted/40 p-3 border border-border">
                <h3 className="font-semibold text-primary mb-1">XML ➡️ JSON</h3>
                <p className="text-muted-foreground text-[11px] leading-normal">
                  Deserializes custom XML elements back into standard JSON datasets.
                </p>
              </div>
              <div className="rounded-md bg-muted/40 p-3 border border-border">
                <h3 className="font-semibold text-primary mb-1">PDF ➡️ TXT</h3>
                <p className="text-muted-foreground text-[11px] leading-normal">
                  Extracts full raw text page-by-page from digital PDF files.
                </p>
              </div>
            </div>
          </Panel>
        </div>
      </div>

      {/* History log panel */}
      <div className="mt-4">
        <Panel title="Conversion Logs" description="Audit log of recent format conversions.">
          {historyLoading ? (
            <div className="h-32 grid place-items-center text-[12.5px] text-muted-foreground">
              <RefreshCw className="w-5 h-5 animate-spin text-primary" />
            </div>
          ) : !history || history.length === 0 ? (
            <div className="h-24 grid place-items-center text-[12.5px] text-muted-foreground">
              No conversion history found. Run a conversion to see it logged.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b text-muted-foreground font-semibold">
                    <th className="py-2.5 px-3">Date/Time</th>
                    <th className="py-2.5 px-3">Original File</th>
                    <th className="py-2.5 px-3">Source</th>
                    <th className="py-2.5 px-3">Target</th>
                    <th className="py-2.5 px-3">Status</th>
                    <th className="py-2.5 px-3">Error / Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {history.map((h: ConversionHistoryRecord) => (
                    <tr key={h.id} className="hover:bg-muted/30 transition-colors">
                      <td className="py-2.5 px-3 text-muted-foreground whitespace-nowrap">
                        {h.created_date ? new Date(h.created_date + "Z").toLocaleString() : "—"}
                      </td>
                      <td className="py-2.5 px-3 font-medium truncate max-w-[200px]" title={h.original_file_name}>
                        {h.original_file_name}
                      </td>
                      <td className="py-2.5 px-3 uppercase font-semibold text-[10px] text-muted-foreground">
                        {h.source_format}
                      </td>
                      <td className="py-2.5 px-3 uppercase font-semibold text-[10px] text-muted-foreground">
                        {h.target_format}
                      </td>
                      <td className="py-2.5 px-3">
                        {h.status === "SUCCESS" ? (
                          <span className="inline-flex items-center gap-1 text-[10.5px] text-success bg-success/10 px-2 py-0.5 rounded font-semibold border border-success/20">
                            <CheckCircle2 className="w-3 h-3" /> Success
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10.5px] text-destructive bg-destructive/10 px-2 py-0.5 rounded font-semibold border border-destructive/20">
                            <XCircle className="w-3 h-3" /> Failed
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 max-w-[250px] truncate text-muted-foreground" title={h.error_message || ""}>
                        {h.error_message || "Completed successfully"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
