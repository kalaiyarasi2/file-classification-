import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Tags, Play, RotateCcw, FileText, Type, Tag, FolderInput } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PdfDropzone } from "@/components/PdfDropzone";
import { api, type ClassifyResponse } from "@/lib/api";
import { useApp, useSettings } from "@/lib/store";
import { toast } from "sonner";

export const Route = createFileRoute("/classification")({ component: ClassificationPage });

function ClassificationPage() {
  const [tab, setTab] = useState("pdf");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [maxPages, setMaxPages] = useState(3);
  const [model, setModel] = useState("");
  const [threshold, setThreshold] = useState<number | "">("");
  const [result, setResult] = useState<ClassifyResponse | null>(null);
  const [organiseResult, setOrganiseResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const addLog = useApp((s) => s.addLog);
  const recordClassification = useApp((s) => s.recordClassification);
  const addActivity = useApp((s) => s.addActivity);
  const defaultOutput = useSettings((s) => s.defaultOutputFolder);

  async function run() {
    setLoading(true); setResult(null); setOrganiseResult(null);
    try {
      const t0 = performance.now();
      let r: ClassifyResponse;
      if (tab === "pdf") {
        if (!file) throw new Error("Select a PDF first");
        r = await api.classifyPdf(file, {
          max_pages: maxPages,
          llm_model: model || undefined,
          threshold: threshold === "" ? undefined : Number(threshold),
        });
      } else {
        if (text.trim().length < 10) throw new Error("Provide at least 10 characters of text");
        r = await api.classifyText(text, model || undefined, threshold === "" ? undefined : Number(threshold));
      }
      const ms = performance.now() - t0;
      setResult(r);
      recordClassification(r.category !== "Others" && !r.error, r.llm_score_0_10, r.category, ms);
      addActivity({ kind: "classification", title: `Classified: ${r.filename || "text"}`, detail: `Category ${r.category} · score ${r.llm_score_0_10}` });
      addLog("INFO", "classification", `Classified: ${r.filename || "text"} - Category ${r.category} · score ${r.llm_score_0_10}`, r);
      // Note: classify_pdf already saves the file server-side; show the destination
      setOrganiseResult({
        action: "copy",
        category: r.category,
        destination_path: `output/${r.category}/${r.filename || ""}`,
      });
    } catch (e: any) { toast.error(e.message); addLog("ERROR", "classification", e.message); }
    finally { setLoading(false); }
  }

  function reset() { setFile(null); setText(""); setResult(null); setOrganiseResult(null); }

  return (
    <>
      <PageHeader icon={Tags} title="Classification" description="Classify a PDF or raw text against your configured categories using GPT-4o." />

      <Panel className="mb-3">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="mb-3">
            <TabsTrigger value="pdf" className="text-[12px]"><FileText className="w-3.5 h-3.5" /> Upload PDF</TabsTrigger>
            <TabsTrigger value="text" className="text-[12px]"><Type className="w-3.5 h-3.5" /> Paste Text</TabsTrigger>
          </TabsList>
          <TabsContent value="pdf" className="mt-0">
            <PdfDropzone file={file} onFiles={(fs) => { setFile(fs[0]); setResult(null); }} />
          </TabsContent>
          <TabsContent value="text" className="mt-0">
            <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste document text…" rows={6} className="text-[12.5px] font-mono" />
          </TabsContent>
        </Tabs>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
          <div>
            <Label className="text-[11.5px]">Maximum Pages</Label>
            <Input type="number" min={1} max={20} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="h-8 mt-1" />
          </div>
          <div>
            <Label className="text-[11.5px]">LLM Model (optional)</Label>
            <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o" className="h-8 mt-1" />
          </div>
          <div>
            <Label className="text-[11.5px]">Threshold (0–10)</Label>
            <Input type="number" min={0} max={10} step={0.1} value={threshold} onChange={(e) => setThreshold(e.target.value === "" ? "" : Number(e.target.value))} className="h-8 mt-1" />
          </div>
        </div>

        <div className="mt-3 flex gap-2 justify-end">
          <Button size="sm" variant="outline" onClick={reset}><RotateCcw className="w-3.5 h-3.5" /> Reset</Button>
          <Button size="sm" onClick={run} disabled={loading}><Play className="w-3.5 h-3.5" /> {loading ? "Classifying…" : "Classify"}</Button>
        </div>
      </Panel>

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <Panel title="Result" className="lg:col-span-2">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              <KV label="Category" value={result.category} accent />
              <KV label="LLM Score" value={result.llm_score_0_10.toString()} />
              <KV label="Confidence" value={`${(result.confidence_score * 100).toFixed(1)}%`} />
              <KV label="Time" value={`${result.classification_time_sec}s`} />
              {result.pdf_type && <KV label="PDF Type" value={result.pdf_type} />}
              {result.rotation_info && <KV label="Rotation" value={result.rotation_info} />}
              {result.filename && <KV label="File" value={result.filename} />}
              {result.error && <KV label="Error" value={result.error} />}
            </div>
            {result.extracted_text && (
              <>
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5">Extracted Text</div>
                <pre className="text-[11.5px] font-mono whitespace-pre-wrap bg-muted/40 rounded-md p-3 max-h-72 overflow-auto leading-relaxed">
                  {result.extracted_text}
                </pre>
              </>
            )}
          </Panel>

          <Panel title="File Organisation" description="Result of post-classification organisation">
            {organiseResult ? (
              <div className="space-y-2 text-[12px]">
                <div className="flex items-center gap-2 p-2 rounded-md bg-success/10 text-success">
                  <FolderInput className="w-4 h-4" />
                  <span className="font-medium">Action performed: {organiseResult.action}</span>
                </div>
                <KV label="Category" value={organiseResult.category} icon={Tag} />
                <KV label="Destination Folder" value={`${defaultOutput}/${organiseResult.category}`} />
                <KV label="Saved File" value={organiseResult.destination_path} />
              </div>
            ) : (
              <div className="text-[12.5px] text-muted-foreground py-6 text-center">No file organisation result yet.</div>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}

function KV({ label, value, accent, icon: Icon }: { label: string; value: string; accent?: boolean; icon?: any }) {
  return (
    <div className="bg-muted/40 rounded-md p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
        {Icon && <Icon className="w-3 h-3" />}{label}
      </div>
      <div className={`text-[13px] font-semibold truncate ${accent ? "text-primary" : ""}`} title={value}>{value}</div>
    </div>
  );
}
