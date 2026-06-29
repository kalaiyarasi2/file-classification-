import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo, useEffect } from "react";
import { Terminal, RefreshCw, Download, Trash2, Search } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useApp } from "@/lib/store";

export const Route = createFileRoute("/logs")({ component: LogsPage });

function LogsPage() {
  const logs = useApp((s) => s.logs);
  const clear = useApp((s) => s.clearLogs);
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState("");
  const [auto, setAuto] = useState(true);
  const [, force] = useState(0);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => force((n) => n + 1), 2000);
    return () => clearInterval(t);
  }, [auto]);

  const filtered = useMemo(
    () => logs
      .filter((l) => !level || l.level === level)
      .filter((l) => !search || l.message.toLowerCase().includes(search.toLowerCase()) || l.source.includes(search)),
    [logs, level, search]
  );

  function download() {
    const text = filtered.map((l) => `[${l.ts}] [${l.level}] ${l.source}: ${l.message}${l.meta ? ` ${JSON.stringify(l.meta)}` : ""}`).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "logs.txt"; a.click();
  }

  const levelColor: Record<string, string> = {
    INFO: "text-primary", WARN: "text-warning", ERROR: "text-destructive", DEBUG: "text-muted-foreground",
  };

  return (
    <>
      <PageHeader
        icon={Terminal}
        title="Logs"
        description="Activity log of detections, extractions, classifications, and pipeline runs in this session."
        actions={
          <>
            <div className="flex items-center gap-2 mr-2">
              <Switch checked={auto} onCheckedChange={setAuto} id="autorefresh" />
              <Label htmlFor="autorefresh" className="text-[11.5px]">Auto refresh</Label>
            </div>
            <Button size="sm" variant="outline" onClick={download}><Download className="w-3.5 h-3.5" /> Download</Button>
            <Button size="sm" variant="outline" onClick={clear} className="text-destructive"><Trash2 className="w-3.5 h-3.5" /> Clear</Button>
          </>
        }
      />

      <Panel
        title="Live Logs"
        description={`${filtered.length} of ${logs.length} entries`}
        actions={
          <>
            <div className="relative">
              <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search logs..." className="h-7 pl-6 text-[12px] w-48" />
            </div>
            <select value={level} onChange={(e) => setLevel(e.target.value)} className="h-7 px-2 rounded-md border border-input bg-background text-[12px]">
              <option value="">All levels</option>
              <option value="INFO">INFO</option><option value="WARN">WARN</option>
              <option value="ERROR">ERROR</option><option value="DEBUG">DEBUG</option>
            </select>
          </>
        }
      >
        {filtered.length === 0 ? (
          <div className="text-[12.5px] text-muted-foreground text-center py-8">No log entries.</div>
        ) : (
          <div className="font-mono text-[11.5px] bg-muted/40 rounded-md p-3 max-h-[480px] overflow-auto space-y-0.5">
            {filtered.map((l, i) => (
              <div key={i} className="leading-relaxed">
                <span className="text-muted-foreground">[{l.ts}]</span>{" "}
                <span className={`${levelColor[l.level]} font-semibold`}>[{l.level}]</span>{" "}
                <span>{l.message}</span>
                {l.meta && <span className="text-muted-foreground"> - {typeof l.meta === "string" ? l.meta : JSON.stringify(l.meta).slice(0, 120)}</span>}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
