import { useDropzone } from "@/hooks/use-dropzone";
import { Upload, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

export function PdfDropzone({
  onFiles, multiple = false, file, accept = ".pdf",
}: {
  onFiles: (files: File[]) => void;
  multiple?: boolean;
  file?: File | null;
  accept?: string;
}) {
  const { rootProps, inputProps, isDragActive } = useDropzone({ onFiles, multiple, accept });
  return (
    <label
      {...rootProps}
      className={cn(
        "flex flex-col items-center justify-center text-center px-4 py-8 rounded-md border border-dashed cursor-pointer transition-colors",
        isDragActive ? "border-primary bg-primary/5" : "border-border bg-muted/30 hover:bg-muted/50"
      )}
    >
      <input {...inputProps} />
      <div className="w-9 h-9 rounded-md bg-primary/10 text-primary grid place-items-center mb-2">
        {file ? <FileText className="w-4 h-4" /> : <Upload className="w-4 h-4" />}
      </div>
      {file ? (
        <>
          <div className="text-[13px] font-medium">{file.name}</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            {(file.size / 1024 / 1024).toFixed(2)} MB · click or drop to replace
          </div>
        </>
      ) : (
        <>
          <div className="text-[13px] font-medium">Drop {multiple ? "PDFs" : "a PDF"} here</div>
          <div className="text-[11.5px] text-muted-foreground mt-0.5">
            or <span className="text-primary font-medium">browse</span> from your device
            {multiple && " · multiple files supported"}
          </div>
          <div className="text-[10.5px] font-mono text-muted-foreground mt-1.5">PDF · max 50 MB / file</div>
        </>
      )}
    </label>
  );
}
