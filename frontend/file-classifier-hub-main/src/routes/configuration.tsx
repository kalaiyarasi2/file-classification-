import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal, Plus, Search, Cpu, Gauge, FileText, Layers } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel, StatCard } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from "@/components/ui/accordion";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { toast } from "sonner";

export const Route = createFileRoute("/configuration")({ component: ConfigPage });

function ConfigPage() {
  const { data } = useQuery({ queryKey: ["config"], queryFn: api.getConfig, retry: false });
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const qc = useQueryClient();

  const add = useMutation({
    mutationFn: () =>
      api.addCategory(name, keywords.split(/[,\n]/).map((k) => k.trim()).filter(Boolean)),
    onSuccess: () => {
      toast.success("Category added");
      setOpen(false); setName(""); setKeywords("");
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = search.toLowerCase();
    if (!q) return data.categories;
    return data.categories.filter(
      (c) => c.name.toLowerCase().includes(q) || c.keywords.some((k) => k.toLowerCase().includes(q))
    );
  }, [data, search]);

  const totalKeywords = data?.categories.reduce((s, c) => s + c.keyword_count, 0) ?? 0;

  return (
    <>
      <PageHeader
        icon={SlidersHorizontal}
        title="Configuration"
        description="Live view of LLM, thresholds and configured categories with keywords."
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button size="sm"><Plus className="w-3.5 h-3.5" /> Add category</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add new category</DialogTitle>
                <DialogDescription>Adds a new category permanently to the backend .env.</DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <div>
                  <Label className="text-[11.5px]">Name</Label>
                  <Input value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="UTILITY_BILLS" className="mt-1 h-8 font-mono" />
                </div>
                <div>
                  <Label className="text-[11.5px]">Keywords (comma or newline separated)</Label>
                  <Textarea value={keywords} onChange={(e) => setKeywords(e.target.value)} rows={5} placeholder="electricity, water, gas" className="mt-1 font-mono text-[12px]" />
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
                <Button size="sm" onClick={() => add.mutate()} disabled={add.isPending}>
                  <Plus className="w-3.5 h-3.5" /> Add category
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <StatCard label="LLM Model" value={data?.llm_model || "—"} icon={Cpu} />
        <StatCard label="Threshold" value={data?.min_score_threshold ?? "—"} icon={Gauge} />
        <StatCard label="Max Pages" value={data?.pdf_max_pages ?? "—"} icon={FileText} />
        <StatCard label="Categories" value={data?.categories.length ?? 0} icon={Layers} hint={`${totalKeywords} keywords total`} />
      </div>

      <Panel
        title="Categories"
        description="Expand to view keywords"
        actions={
          <div className="relative">
            <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search categories or keywords..." className="h-7 pl-6 text-[12px] w-64" />
          </div>
        }
      >
        {filtered.length === 0 ? (
          <div className="text-[12.5px] text-muted-foreground text-center py-6">No categories.</div>
        ) : (
          <Accordion type="multiple" className="w-full">
            {filtered.map((c) => (
              <AccordionItem key={c.name} value={c.name}>
                <AccordionTrigger className="text-[12.5px] hover:no-underline py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold">{c.name}</span>
                    <span className="text-[10.5px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground tabular-nums">{c.keyword_count}</span>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {c.keywords.map((k) => (
                      <span key={k} className="text-[11px] px-1.5 py-0.5 rounded bg-muted font-mono">{k}</span>
                    ))}
                  </div>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </Panel>
    </>
  );
}
