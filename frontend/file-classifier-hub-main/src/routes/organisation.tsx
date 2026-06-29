import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { FolderTree, ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useQuery } from "@tanstack/react-query";
import { api, type OrganiseResponse } from "@/lib/api";
import { useApp, useSettings } from "@/lib/store";
import { toast } from "sonner";

export const Route = createFileRoute("/organisation")({ component: OrgPage });

function OrgPage() {
  const defaultOut = useSettings((s) => s.defaultOutputFolder);
  const [source, setSource] = useState("");
  const [category, setCategory] = useState("");
  const [output, setOutput] = useState(defaultOut);
  const [copyMode, setCopyMode] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [result, setResult] = useState<OrganiseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const addLog = useApp((s) => s.addLog);
  const addActivity = useApp((s) => s.addActivity);

  const { data: cats } = useQuery({ queryKey: ["categories"], queryFn: api.listCategories, retry: false });

  async function run() {
    if (!source || !category || !output) return toast.error("Fill all fields");
    setLoading(true);
    try {
      const r = await api.organise({ source_path: source, category, output_folder: output, copy_mode: copyMode, dry_run: dryRun });
      setResult(r);
      addActivity({ kind: "organise", title: `Organise: ${source}`, detail: `${r.action} → ${r.destination_path}` });
      addLog("INFO", "organisation", `${r.action} → ${r.destination_path}`, r);
      toast.success("Organised");
    } catch (e: any) { toast.error(e.message); addLog("ERROR", "organisation", e.message); }
    finally { setLoading(false); }
  }

  return (
    <>
      <PageHeader icon={FolderTree} title="File Organisation" description="Move or copy a single file into its category folder under the output root." />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel title="Operation" description="Configure source, target category and output">
          <div className="space-y-3">
            <div>
              <Label className="text-[11.5px]">Source Path</Label>
              <Input value={source} onChange={(e) => setSource(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" placeholder="/data/incoming/invoice_001.pdf" />
            </div>
            <div>
              <Label className="text-[11.5px]">Category</Label>
              <select value={category} onChange={(e) => setCategory(e.target.value)} className="w-full h-8 mt-1 px-2 rounded-md border border-input bg-background text-[12.5px]">
                <option value="">Select a category</option>
                {cats?.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <Label className="text-[11.5px]">Output Folder</Label>
              <Input value={output} onChange={(e) => setOutput(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" />
            </div>
            <div className="flex gap-6">
              <div className="flex items-center gap-2"><Switch checked={copyMode} onCheckedChange={setCopyMode} /><Label className="text-[11.5px]">Copy Mode</Label></div>
              <div className="flex items-center gap-2"><Switch checked={dryRun} onCheckedChange={setDryRun} /><Label className="text-[11.5px]">Dry Run</Label></div>
            </div>
            <div className="flex justify-end">
              <Button size="sm" onClick={run} disabled={loading}><ArrowRight className="w-3.5 h-3.5" /> {copyMode ? "Copy" : "Move"}</Button>
            </div>
          </div>
        </Panel>

        <Panel title="Result" description="Operation summary">
          {!result ? (
            <div className="text-[12.5px] text-muted-foreground text-center py-10">No operation yet.</div>
          ) : (
            <div className="space-y-2 text-[12.5px]">
              <KV label="Action" value={result.action} />
              <KV label="Category" value={result.category} />
              <KV label="Source" value={result.source_path} />
              <KV label="Destination" value={result.destination_path} />
              <KV label="Dry Run" value={result.dry_run ? "Yes" : "No"} />
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-muted-foreground text-[12px]">{label}</span>
      <span className="font-mono text-[12px] text-right truncate ml-3 max-w-[60%]">{value}</span>
    </div>
  );
}
