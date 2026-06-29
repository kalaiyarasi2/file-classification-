import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { FileText, Play, RotateCcw, Search, Copy, Download } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { PdfDropzone } from "@/components/PdfDropzone";
import { api, type ExtractResponse } from "@/lib/api";
import { useApp } from "@/lib/store";
import { toast } from "sonner";

export const Route = createFileRoute("/extraction")({ component: ExtractionPage });

function ExtractionPage() {
  const [file, setFile] = useState<File | null>(null);
  const [maxPages, setMaxPages] = useState(3);
  const [forceOcr, setForceOcr] = useState(false);
  const [autoRot, setAutoRot] = useState(true);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<ExtractResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const addLog = useApp((s) => s.addLog);
  const recordExtraction = useApp((s) => s.recordExtraction);
  const addActivity = useApp((s) => s.addActivity);

  async function run() {
    if (!file) return toast.error("Select a PDF first");
    setLoading(true);
    try {
      const t0 = performance.now();
      const r = await api.extract(file, { max_pages: maxPages, force_ocr: forceOcr, use_auto_rotation: autoRot });
      const ms = performance.now() - t0;
      setResult(r);
      recordExtraction(r.pdf_type, ms);
      addActivity({ kind: "extraction", title: `Extraction: ${file.name}`, detail: `${r.char_count} chars · ${r.pdf_type}` });
      addLog("INFO", "extraction", `Extraction: ${file.name} - ${r.char_count} chars · ${r.pdf_type}`, r);
    } catch (e: any) { toast.error(e.message); addLog("ERROR", "extraction", e.message); }
    finally { setLoading(false); }
  }

  function reset() { setFile(null); setResult(null); setSearch(""); }

  const highlighted = useMemo(() => {
    if (!result) return "";
    if (!search.trim()) return result.text_full;
    const re = new RegExp(`(${search.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
    return result.text_full.replace(re, "⟦$1⟧");
  }, [result, search]);

  function copy() {
    if (result) { navigator.clipboard.writeText(result.text_full); toast.success("Text copied"); }
  }
  function download() {
    if (!result) return;
    const blob = new Blob([result.text_full], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${result.filename.replace(/\.pdf$/i, "")}.txt`; a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader icon={FileText} title="Text Extraction" description="Extract text from digital or scanned PDFs with optional OCR and auto-rotation." />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel title="Input" description="PDF file & extraction options">
          <PdfDropzone file={file} onFiles={(fs) => { setFile(fs[0]); setResult(null); }} />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
            <div>
              <Label className="text-[11.5px]">Maximum Pages</Label>
              <Input type="number" min={1} max={20} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="h-8 mt-1" />
            </div>
            <div className="flex items-end gap-2 pb-1">
              <Switch checked={forceOcr} onCheckedChange={setForceOcr} />
              <Label className="text-[11.5px]">Force OCR</Label>
            </div>
            <div className="flex items-end gap-2 pb-1">
              <Switch checked={autoRot} onCheckedChange={setAutoRot} />
              <Label className="text-[11.5px]">Auto Rotation</Label>
            </div>
          </div>
          <div className="mt-3 flex gap-2 justify-end">
            <Button size="sm" variant="outline" onClick={reset}><RotateCcw className="w-3.5 h-3.5" /> Reset</Button>
            <Button size="sm" onClick={run} disabled={!file || loading}>
              <Play className="w-3.5 h-3.5" /> {loading ? "Extracting…" : "Extract"}
            </Button>
          </div>
        </Panel>

        <Panel
          title="Output"
          description="Extracted text preview"
          actions={
            result && (
              <>
                <div className="relative">
                  <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search in text" className="h-7 pl-6 text-[12px] w-44" />
                </div>
                <Button size="sm" variant="outline" onClick={copy}><Copy className="w-3 h-3" /> Copy</Button>
                <Button size="sm" variant="outline" onClick={download}><Download className="w-3 h-3" /> Download</Button>
              </>
            )
          }
        >
          {!result ? (
            <div className="text-[12.5px] text-muted-foreground text-center py-10">Run extraction to see text here.</div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2 mb-3 text-[11.5px]">
                <Stat label="PDF Type" value={result.pdf_type} />
                <Stat label="Characters" value={result.char_count.toLocaleString()} />
                <Stat label="Time" value={`${result.extraction_time_sec}s`} />
              </div>
              <div className="text-[11px] text-muted-foreground mb-2">Rotation: {result.rotation_info || "—"}</div>
              <pre className="text-[11.5px] font-mono whitespace-pre-wrap bg-muted/40 rounded-md p-3 max-h-[400px] overflow-auto leading-relaxed">
                {highlighted}
              </pre>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-muted/40 rounded-md p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-[13px] font-semibold capitalize">{value}</div>
    </div>
  );
}
