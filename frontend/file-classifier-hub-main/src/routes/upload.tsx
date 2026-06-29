import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Upload, X, FileText, MoreHorizontal, Sparkles, Trash2, Play, ScanSearch, Tags } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { PdfDropzone } from "@/components/PdfDropzone";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";

export const Route = createFileRoute("/upload")({ component: UploadPage });

interface QueuedFile { id: string; file: File; status: "queued" | "running" | "done" | "error"; result?: string; }

function UploadPage() {
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const navigate = useNavigate();
  const addLog = useApp((s) => s.addLog);
  const addActivity = useApp((s) => s.addActivity);
  const recordClassification = useApp((s) => s.recordClassification);
  const recordDetection = useApp((s) => s.recordDetection);
  const recordExtraction = useApp((s) => s.recordExtraction);

  function addFiles(files: File[]) {
    const tooBig = files.filter((f) => f.size > 50 * 1024 * 1024);
    if (tooBig.length) toast.error(`${tooBig.length} file(s) exceed 50 MB`);
    const ok = files.filter((f) => f.size <= 50 * 1024 * 1024);
    setQueue((q) => [...q, ...ok.map((f) => ({ id: crypto.randomUUID(), file: f, status: "queued" as const }))]);
  }

  async function runOne(id: string, action: "classify" | "extract" | "detect") {
    const item = queue.find((q) => q.id === id);
    if (!item) return;
    setQueue((q) => q.map((x) => x.id === id ? { ...x, status: "running" } : x));
    try {
      if (action === "classify") {
        const t0 = performance.now();
        const r = await api.classifyPdf(item.file);
        const ms = performance.now() - t0;
        recordClassification(!!r.category && r.category !== "Others", r.llm_score_0_10, r.category, ms);
        addActivity({ kind: "classification", title: `Classified: ${item.file.name}`, detail: `Category ${r.category} · score ${r.llm_score_0_10}` });
        addLog("INFO", "upload", `Classified: ${item.file.name}`, r);
        setQueue((q) => q.map((x) => x.id === id ? { ...x, status: "done", result: `${r.category} (${r.llm_score_0_10})` } : x));
      } else if (action === "extract") {
        const t0 = performance.now();
        const r = await api.extract(item.file, { max_pages: 3, use_auto_rotation: true });
        const ms = performance.now() - t0;
        recordExtraction(r.pdf_type, ms);
        addActivity({ kind: "extraction", title: `Extraction: ${item.file.name}`, detail: `${r.char_count} chars · ${r.pdf_type}` });
        addLog("INFO", "upload", `Extraction: ${item.file.name}`, { chars: r.char_count, type: r.pdf_type });
        setQueue((q) => q.map((x) => x.id === id ? { ...x, status: "done", result: `${r.char_count} chars · ${r.pdf_type}` } : x));
      } else {
        const r = await api.detect(item.file);
        recordDetection(r.is_digital);
        addActivity({ kind: "detection", title: `Detection: ${item.file.name}`, detail: `Type ${r.pdf_type}` });
        addLog("INFO", "upload", `Detection: ${item.file.name}`, r);
        setQueue((q) => q.map((x) => x.id === id ? { ...x, status: "done", result: `Type: ${r.pdf_type}` } : x));
      }
    } catch (e: any) {
      addLog("ERROR", "upload", `${action} failed: ${item.file.name}`, e.message);
      toast.error(`Failed: ${e.message}`);
      setQueue((q) => q.map((x) => x.id === id ? { ...x, status: "error", result: e.message } : x));
    }
  }

  async function classifyAll() {
    for (const item of queue.filter((q) => q.status === "queued")) {
      await runOne(item.id, "classify");
    }
    toast.success("All classifications complete");
    setTimeout(() => navigate({ to: "/classification" }), 500);
  }

  return (
    <>
      <PageHeader
        icon={Upload}
        title="Upload"
        description="Drag and drop PDFs to detect, extract or classify. Maximum 50 MB per file."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => setQueue([])} disabled={queue.length === 0}>
              <Trash2 className="w-3.5 h-3.5" /> Clear queue
            </Button>
            <Button size="sm" onClick={classifyAll} disabled={queue.length === 0}>
              <Sparkles className="w-3.5 h-3.5" /> Classify all
            </Button>
          </>
        }
      />

      <Panel className="mb-3">
        <PdfDropzone onFiles={addFiles} multiple />
      </Panel>

      <Panel title="Upload Queue" description={`${queue.length} file${queue.length === 1 ? "" : "s"} queued`}>
        {queue.length === 0 ? (
          <div className="text-[12.5px] text-muted-foreground text-center py-6">No files queued. Drop PDFs above.</div>
        ) : (
          <ul className="divide-y divide-border">
            {queue.map((q) => (
              <li key={q.id} className="flex items-center gap-3 py-2.5">
                <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium truncate">{q.file.name}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {(q.file.size / 1024 / 1024).toFixed(2)} MB
                    {q.result && ` · ${q.result}`}
                  </div>
                </div>
                <span className={`text-[10.5px] px-1.5 py-0.5 rounded font-medium tracking-wider ${
                  q.status === "done" ? "bg-success/15 text-success" :
                  q.status === "running" ? "bg-primary/15 text-primary" :
                  q.status === "error" ? "bg-destructive/15 text-destructive" :
                  "bg-muted text-muted-foreground"
                }`}>{q.status.toUpperCase()}</span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm" variant="outline" className="h-7">Action <MoreHorizontal className="w-3 h-3" /></Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuLabel className="text-[11px] text-muted-foreground">Run</DropdownMenuLabel>
                    <DropdownMenuItem onClick={() => runOne(q.id, "classify")}><Tags className="w-3.5 h-3.5" /> Classify</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => runOne(q.id, "extract")}><FileText className="w-3.5 h-3.5" /> Extract text</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => runOne(q.id, "detect")}><ScanSearch className="w-3.5 h-3.5" /> Detect type</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem className="text-destructive" onClick={() => setQueue((Q) => Q.filter((x) => x.id !== q.id))}>
                      <X className="w-3.5 h-3.5" /> Remove
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <button onClick={() => setQueue((Q) => Q.filter((x) => x.id !== q.id))} className="p-1 rounded hover:bg-accent text-muted-foreground" aria-label="Remove">
                  <X className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 text-[10.5px] text-muted-foreground font-mono">Supported formats: .pdf</div>
      </Panel>
    </>
  );
}
