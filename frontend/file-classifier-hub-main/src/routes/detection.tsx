import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { ScanSearch, RotateCcw, Play, FileText, ScanLine, Timer } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { PdfDropzone } from "@/components/PdfDropzone";
import { api, type DetectResponse } from "@/lib/api";
import { useApp } from "@/lib/store";
import { toast } from "sonner";

export const Route = createFileRoute("/detection")({ component: DetectionPage });

function DetectionPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<DetectResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const addLog = useApp((s) => s.addLog);
  const recordDetection = useApp((s) => s.recordDetection);
  const addActivity = useApp((s) => s.addActivity);

  async function run() {
    if (!file) return toast.error("Select a PDF first");
    setLoading(true);
    try {
      const r = await api.detect(file);
      setResult(r);
      recordDetection(r.is_digital);
      addActivity({ kind: "detection", title: `Detection: ${file.name}`, detail: `Type ${r.pdf_type}` });
      addLog("INFO", "detection", `Detection: ${file.name} - Type ${r.pdf_type}`, r);
    } catch (e: any) { toast.error(e.message); addLog("ERROR", "detection", e.message); }
    finally { setLoading(false); }
  }

  return (
    <>
      <PageHeader icon={ScanSearch} title="PDF Detection" description="Determine whether a PDF has a real text layer (digital) or is image-only (scanned)." />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel title="Input" description="Upload a single PDF (max 50 MB)">
          <PdfDropzone file={file} onFiles={(fs) => { setFile(fs[0]); setResult(null); }} />
          <div className="mt-3 flex gap-2 justify-end">
            <Button size="sm" variant="outline" onClick={() => { setFile(null); setResult(null); }}>
              <RotateCcw className="w-3.5 h-3.5" /> Reset
            </Button>
            <Button size="sm" onClick={run} disabled={!file || loading}>
              <Play className="w-3.5 h-3.5" /> {loading ? "Detecting…" : "Detect"}
            </Button>
          </div>
        </Panel>

        <Panel title="Result" description="Detection output">
          {!result ? (
            <div className="text-[12.5px] text-muted-foreground text-center py-10">
              Run detection to see the result here.
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-3 p-3 rounded-md bg-muted/40">
                {result.is_digital ? <FileText className="w-5 h-5 text-primary" /> : <ScanLine className="w-5 h-5 text-warning" />}
                <div>
                  <div className="text-[11px] text-muted-foreground">PDF Type</div>
                  <div className="text-[15px] font-semibold capitalize">{result.pdf_type}</div>
                </div>
              </div>
              <DataRow label="Filename" value={result.filename} />
              <DataRow label="Is Digital" value={result.is_digital ? "Yes" : "No"} />
              <DataRow label="Detection Time" value={`${result.detection_time_sec}s`} icon={Timer} />
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

function DataRow({ label, value, icon: Icon }: { label: string; value: string; icon?: any }) {
  return (
    <div className="flex items-center justify-between text-[12.5px] py-1.5 border-b border-border last:border-0">
      <span className="text-muted-foreground flex items-center gap-1.5">{Icon && <Icon className="w-3 h-3" />}{label}</span>
      <span className="font-medium font-mono">{value}</span>
    </div>
  );
}
