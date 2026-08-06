import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Workflow, Play, FolderInput, FolderOutput, Cpu, Gauge, Search } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Progress } from "@/components/ui/progress";
import { api, type PipelineResponse } from "@/lib/api";
import { useApp, useSettings } from "@/lib/store";
import { toast } from "sonner";
import { FolderPickerModal } from "@/components/FolderPickerModal";

export const Route = createFileRoute("/pipeline")({ component: PipelinePage });

const STEPS = ["Detect", "Extract", "Classify", "Organise", "Report"];

function PipelinePage() {
  const defaultIn = useSettings((s) => s.defaultInputFolder);
  const defaultOut = useSettings((s) => s.defaultOutputFolder);

  const [input, setInput] = useState(defaultIn);
  const [output, setOutput] = useState(defaultOut);
  const [maxPages, setMaxPages] = useState(3);
  const [model, setModel] = useState("gpt-4o");
  const [minScore, setMinScore] = useState(7.0);
  const [copyMode, setCopyMode] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [result, setResult] = useState<PipelineResponse | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerType, setPickerType] = useState<"input" | "output">("input");
  const [pipelineMode, setPipelineMode] = useState<"server" | "client">("server");
  const [clientInputHandle, setClientInputHandle] = useState<FileSystemDirectoryHandle | null>(null);
  const [clientOutputHandle, setClientOutputHandle] = useState<FileSystemDirectoryHandle | null>(null);

  const isDirectoryPickerSupported = typeof window !== "undefined" && !!(window as any).showDirectoryPicker;

  const selectClientFolder = async (type: "input" | "output") => {
    if (!isDirectoryPickerSupported) {
      toast.error("Your browser does not support the File System Access API. Please use Chrome, Edge, or Opera.");
      return;
    }
    try {
      const handle = await (window as any).showDirectoryPicker({ mode: "readwrite" });
      if (type === "input") {
        setClientInputHandle(handle);
      } else {
        setClientOutputHandle(handle);
      }
      toast.success(`Selected local folder: ${handle.name}`);
    } catch (e: any) {
      if (e.name !== "AbortError") {
        toast.error("Error selecting folder: " + e.message);
      }
    }
  };

  const addLog = useApp((s) => s.addLog);
  const recordPipeline = useApp((s) => s.recordPipeline);
  const addActivity = useApp((s) => s.addActivity);

  const selectLocalFolder = (type: "input" | "output") => {
    setPickerType(type);
    setPickerOpen(true);
  };

  const handleFolderSelect = (path: string) => {
    if (pickerType === "input") {
      setInput(path);
    } else {
      setOutput(path);
    }
  };

  async function run() {
    setRunning(true); setResult(null); setLogs([]); setStepIdx(0);
    const log = (m: string) => setLogs((L) => [...L, `[${new Date().toLocaleTimeString()}] ${m}`]);
    log(`Starting pipeline on ${input}`);
    // simulate step progression while waiting
    const tick = setInterval(() => setStepIdx((i) => Math.min(i + 1, STEPS.length - 1)), 1200);
    try {
      const r = await api.pipeline({
        input_folder: input, output_folder: output, pdf_max_pages: maxPages,
        min_score: minScore, llm_model: model, copy_mode: copyMode, dry_run: dryRun,
      });
      setResult(r);
      recordPipeline(r.successful, r.failed, r.categories_found);
      addActivity({ kind: "pipeline", title: "Pipeline complete", detail: `${r.successful}/${r.total_files} ok · ${r.failed} failed` });
      addLog("INFO", "pipeline", `Pipeline complete - ${r.successful} ok, ${r.failed} failed`, r);
      log(`Done. ${r.successful}/${r.total_files} ok, ${r.failed} failed in ${r.total_time_sec}s`);
      setStepIdx(STEPS.length - 1);
      toast.success("Pipeline complete");
    } catch (e: any) {
      log(`Error: ${e.message}`);
      addLog("ERROR", "pipeline", e.message);
      toast.error(e.message);
    } finally { clearInterval(tick); setRunning(false); }
  }

  async function runClientPipeline() {
    if (!clientInputHandle || !clientOutputHandle) {
      toast.error("Please select both local input and output folders.");
      return;
    }
    setRunning(true); setResult(null); setLogs([]); setStepIdx(0);
    const log = (m: string) => setLogs((L) => [...L, `[${new Date().toLocaleTimeString()}] ${m}`]);
    log(`Starting Client-side local pipeline...`);
    const runId = crypto.randomUUID ? crypto.randomUUID() : "client-" + Math.random().toString(36).substring(2, 15);

    let files: any[] = [];
    let successful = 0;
    let failed = 0;

    try {
      log("Requesting permission to access local directory...");
      const inputPerm = await (clientInputHandle as any).requestPermission({ mode: "readwrite" });
      const outputPerm = await (clientOutputHandle as any).requestPermission({ mode: "readwrite" });
      if (inputPerm !== "granted" || outputPerm !== "granted") {
        throw new Error("Permissions to access directories were not granted.");
      }

      log("Scanning input directory for PDF files...");
      for await (const entry of (clientInputHandle as any).values()) {
        if (entry.kind === "file" && entry.name.toLowerCase().endsWith(".pdf")) {
          files.push(entry);
        }
      }

      log(`Found ${files.length} PDF files in input folder.`);
      if (files.length === 0) {
        log("No PDFs found to process.");
        toast.info("No PDF files found in the input folder.");
        setRunning(false);
        return;
      }

      const categoriesFound: Record<string, number> = {};
      const resultsList = [];
      let activeFileIdx = 0;

      for (const entry of files) {
        log(`Processing [${activeFileIdx + 1}/${files.length}]: ${entry.name}...`);
        const fileT0 = performance.now();
        try {
          const fileObj = await entry.getFile();

          log(`  Uploading & classifying via API...`);
          const classification = await api.classifyPdf(fileObj, {
            max_pages: maxPages,
            llm_model: model,
            threshold: minScore,
            run_id: runId
          });

          if (classification.error) {
            throw new Error(classification.error);
          }

          const category = classification.category || "Others";
          log(`  Classified as: ${category} (Score: ${classification.llm_score_0_10})`);

          if (!dryRun) {
            log(`  Writing to output folder: ${category}/${entry.name}...`);
            const categoryHandle = await clientOutputHandle.getDirectoryHandle(category, { create: true });
            const targetFileHandle = await categoryHandle.getFileHandle(entry.name, { create: true });
            const writable = await targetFileHandle.createWritable();
            await writable.write(fileObj);
            await writable.close();

            if (!copyMode) {
              log(`  Deleting original file from input folder...`);
              await clientInputHandle.removeEntry(entry.name);
            }
          } else {
            log(`  [Dry Run] Simulated moving to: ${category}/${entry.name}`);
          }

          successful++;
          categoriesFound[category] = (categoriesFound[category] || 0) + 1;
          
          resultsList.push({
            file_name: entry.name,
            original_path: `[Local]/${entry.name}`,
            pdf_type: classification.pdf_type || "unknown",
            category: category,
            llm_score: classification.llm_score_0_10,
            destination_folder: `[Local]/${category}`,
            rotation_applied: classification.rotation_info || "none",
            processing_time: (performance.now() - fileT0) / 1000,
            error: ""
          });

        } catch (fileErr: any) {
          failed++;
          log(`  Error processing file ${entry.name}: ${fileErr.message}`);
          resultsList.push({
            file_name: entry.name,
            original_path: `[Local]/${entry.name}`,
            pdf_type: "unknown",
            category: "Error",
            llm_score: 0,
            destination_folder: "",
            rotation_applied: "none",
            processing_time: 0,
            error: fileErr.message || "Failed"
          });
        }
        activeFileIdx++;
        setStepIdx(Math.min(Math.floor((activeFileIdx / files.length) * STEPS.length), STEPS.length - 1));
      }

      const totalTimeSec = resultsList.reduce((acc, curr) => acc + curr.processing_time, 0);
      const summaryResult = {
        total_files: files.length,
        successful: successful,
        failed: failed,
        categories_found: categoriesFound,
        total_time_sec: Number(totalTimeSec.toFixed(2)),
        results: resultsList
      };

      setResult(summaryResult);
      recordPipeline(successful, failed, categoriesFound);
      addActivity({
        kind: "pipeline",
        title: "Local client pipeline complete",
        detail: `${successful}/${files.length} ok · ${failed} failed`
      });
      addLog("INFO", "pipeline", `Local pipeline complete - ${successful} ok, ${failed} failed`, summaryResult);
      log(`Done. ${successful}/${files.length} ok, ${failed} failed in ${totalTimeSec.toFixed(2)}s`);
      setStepIdx(STEPS.length - 1);
      toast.success("Local client pipeline complete!");

      try {
        await api.monitorFinish({
          run_id: runId,
          status: "completed",
          attachments: files.length,
          files_classified: successful,
          errors: failed
        });
      } catch (mdbErr) {
        console.warn("Failed to finalize monitor run:", mdbErr);
      }

    } catch (e: any) {
      log(`Pipeline execution failed: ${e.message}`);
      addLog("ERROR", "pipeline", e.message);
      toast.error(e.message);

      try {
        await api.monitorFinish({
          run_id: runId,
          status: "error",
          attachments: files ? files.length : 0,
          files_classified: typeof successful !== "undefined" ? successful : 0,
          errors: typeof failed !== "undefined" ? failed : 0
        });
      } catch (mdbErr) {
        console.warn("Failed to finalize monitor run on error:", mdbErr);
      }
    } finally {
      setRunning(false);
    }
  }

  async function handleRun() {
    if (pipelineMode === "server") {
      await run();
    } else {
      await runClientPipeline();
    }
  }

  const progress = running ? ((stepIdx + 1) / STEPS.length) * 100 : result ? 100 : 0;

  return (
    <>
      <PageHeader icon={Workflow} title="Pipeline" description="Run detect → extract → classify → organise across an entire folder." />

      <Panel title="Pipeline Configuration" description={pipelineMode === "server" ? "Absolute paths on the backend host" : "Local folders on your client machine"} className="mb-3">
        <div className="flex gap-2 mb-4 border-b pb-3">
          <Button
            size="sm"
            variant={pipelineMode === "server" ? "default" : "outline"}
            className="text-[11px] h-7 px-3"
            onClick={() => setPipelineMode("server")}
            disabled={running}
          >
            Host Server Mode
          </Button>
          <Button
            size="sm"
            variant={pipelineMode === "client" ? "default" : "outline"}
            className="text-[11px] h-7 px-3"
            onClick={() => setPipelineMode("client")}
            disabled={running}
          >
            Client Browser Mode
          </Button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <Label className="text-[11.5px] flex items-center gap-1"><FolderInput className="w-3 h-3" /> Input Folder</Label>
            {pipelineMode === "server" ? (
              <div className="flex gap-2 items-center mt-1">
                <Input value={input} onChange={(e) => setInput(e.target.value)} className="h-8 font-mono text-[12px] flex-1" />
                <Button size="icon" variant="outline" className="h-8 w-8 shrink-0 shadow-sm hover:bg-accent" onClick={() => selectLocalFolder("input")}>
                  <Search className="h-4 w-4" />
                </Button>
              </div>
            ) : (
              <div className="flex gap-2 items-center mt-1">
                <div className="h-8 flex-1 px-3 border rounded-md flex items-center text-[12px] bg-muted/40 font-mono text-muted-foreground truncate">
                  {clientInputHandle ? `📁 [Local] ${clientInputHandle.name}` : "No folder selected"}
                </div>
                <Button size="sm" variant="outline" className="h-8 shadow-sm hover:bg-accent text-xs" onClick={() => selectClientFolder("input")} disabled={running}>
                  Select Folder
                </Button>
              </div>
            )}
          </div>
          <div>
            <Label className="text-[11.5px] flex items-center gap-1"><FolderOutput className="w-3 h-3" /> Output Folder</Label>
            {pipelineMode === "server" ? (
              <div className="flex gap-2 items-center mt-1">
                <Input value={output} onChange={(e) => setOutput(e.target.value)} className="h-8 font-mono text-[12px] flex-1" />
                <Button size="icon" variant="outline" className="h-8 w-8 shrink-0 shadow-sm hover:bg-accent" onClick={() => selectLocalFolder("output")}>
                  <Search className="h-4 w-4" />
                </Button>
              </div>
            ) : (
              <div className="flex gap-2 items-center mt-1">
                <div className="h-8 flex-1 px-3 border rounded-md flex items-center text-[12px] bg-muted/40 font-mono text-muted-foreground truncate">
                  {clientOutputHandle ? `📁 [Local] ${clientOutputHandle.name}` : "No folder selected"}
                </div>
                <Button size="sm" variant="outline" className="h-8 shadow-sm hover:bg-accent text-xs" onClick={() => selectClientFolder("output")} disabled={running}>
                  Select Folder
                </Button>
              </div>
            )}
          </div>
          <div>
            <Label className="text-[11.5px]">Maximum Pages</Label>
            <Input type="number" min={1} max={20} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="h-8 mt-1" />
          </div>
          <div>
            <Label className="text-[11.5px] flex items-center gap-1"><Cpu className="w-3 h-3" /> LLM Model</Label>
            <Input value={model} onChange={(e) => setModel(e.target.value)} className="h-8 mt-1" />
          </div>
          <div>
            <Label className="text-[11.5px] flex items-center gap-1"><Gauge className="w-3 h-3" /> Minimum Score</Label>
            <Input type="number" min={0} max={10} step={0.1} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="h-8 mt-1" />
          </div>
          <div className="flex gap-6 items-end pb-1">
            <div className="flex items-center gap-2"><Switch checked={copyMode} onCheckedChange={setCopyMode} /><Label className="text-[11.5px]">Copy Mode (keep originals)</Label></div>
            <div className="flex items-center gap-2"><Switch checked={dryRun} onCheckedChange={setDryRun} /><Label className="text-[11.5px]">Dry Run (classify only)</Label></div>
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            onClick={handleRun}
            disabled={running || (pipelineMode === "server" ? (!input || !output) : (!clientInputHandle || !clientOutputHandle))}
          >
            <Play className="w-3.5 h-3.5" /> {running ? "Running…" : "Run Pipeline"}
          </Button>
        </div>
      </Panel>

      {(running || result) && (
        <Panel title="Operation Summary" description="Live pipeline status" className="mb-3">
          <div className="mb-3">
            <div className="flex items-center justify-between text-[11.5px] mb-1">
              <span className="font-medium">{running ? STEPS[stepIdx] : "Complete"}</span>
              <span className="text-muted-foreground">{Math.round(progress)}%</span>
            </div>
            <Progress value={progress} className="h-1.5" />
          </div>
          <div className="flex flex-wrap gap-2 mb-3">
            {STEPS.map((s, i) => (
              <span key={s} className={`text-[10.5px] px-2 py-0.5 rounded font-medium tracking-wider ${
                (running && i < stepIdx) || (result && i <= STEPS.length - 1)
                  ? "bg-success/15 text-success"
                  : running && i === stepIdx ? "bg-primary/15 text-primary"
                  : "bg-muted text-muted-foreground"
              }`}>{s.toUpperCase()}</span>
            ))}
          </div>
          {result && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11.5px]">
              <KV label="Total" value={result.total_files.toString()} />
              <KV label="Successful" value={result.successful.toString()} />
              <KV label="Failed" value={result.failed.toString()} />
              <KV label="Time" value={`${result.total_time_sec}s`} />
            </div>
          )}
        </Panel>
      )}

      {!running && !result && (
        <Panel><div className="text-[12.5px] text-muted-foreground text-center py-6">No pipeline execution has been started.</div></Panel>
      )}

      {logs.length > 0 && (
        <Panel title="Execution Logs" className="mb-3">
          <pre className="text-[11.5px] font-mono whitespace-pre-wrap bg-muted/40 rounded-md p-3 max-h-64 overflow-auto">
            {logs.join("\n")}
          </pre>
        </Panel>
      )}

      {result && (
        <Panel title="Final Report" description={`${result.results.length} files processed`}>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border">
                  <th className="py-2 px-2 font-medium">File</th>
                  <th className="py-2 px-2 font-medium">Type</th>
                  <th className="py-2 px-2 font-medium">Category</th>
                  <th className="py-2 px-2 font-medium text-right">Score</th>
                  <th className="py-2 px-2 font-medium text-right">Time</th>
                  <th className="py-2 px-2 font-medium">Destination</th>
                </tr>
              </thead>
              <tbody>
                {result.results.map((r, i) => (
                  <tr key={i} className="border-b border-border last:border-0">
                    <td className="py-1.5 px-2 font-mono truncate max-w-[200px]">{r.file_name}</td>
                    <td className="py-1.5 px-2 capitalize">{r.pdf_type}</td>
                    <td className="py-1.5 px-2 font-medium text-primary">{r.category}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{r.llm_score.toFixed(1)}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{r.processing_time.toFixed(2)}s</td>
                    <td className="py-1.5 px-2 font-mono text-muted-foreground truncate max-w-[200px]">{r.destination_folder}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <FolderPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={handleFolderSelect}
        title={pickerType === "input" ? "Select Input Folder" : "Select Output Folder"}
        initialPath={pickerType === "input" ? input : output}
      />
    </>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-muted/40 rounded-md p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-[14px] font-semibold tabular-nums">{value}</div>
    </div>
  );
}
