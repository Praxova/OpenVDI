import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";


export interface DataTableColumn<T> {
  /** Column header text (rendered in <th>). */
  header: string;
  /** Cell renderer. Receives the row item; returns the cell content. */
  cell: (item: T) => ReactNode;
  /** Right-align (used for the action column conventionally). */
  align?: "left" | "right";
}


interface DataTableProps<T> {
  /** Column definitions. Order is rendering order. */
  columns: DataTableColumn<T>[];
  /** Row data. Empty array renders the empty state. */
  data: T[] | undefined;
  isPending: boolean;
  error: Error | null;
  /** Called when the user clicks the retry button on the error state. */
  onRetry?: () => void;
  /** Stable React key per row. Required for list reconciliation. */
  rowKey: (item: T) => string;
  /** Custom empty-state message. Defaults to "No items." */
  emptyMessage?: string;
  /** Optional className for outer wrapper. */
  className?: string;
}


/**
 * Resource-list table primitive used by admin list pages. Per FE4.
 *
 * Renders four states:
 *   - isPending: skeleton rows
 *   - error: alert + retry button
 *   - empty: muted message
 *   - data: <table> with column headers + rows
 *
 * Generic over the row type T. Each column declares a `cell` render
 * function that receives a row and returns the cell content.
 *
 * Forward-compat: M4-20 onward may add sort / filter props. v0 ships
 * the four-state table only.
 */
export function DataTable<T>({
  columns,
  data,
  isPending,
  error,
  onRetry,
  rowKey,
  emptyMessage = "No items.",
  className = "",
}: DataTableProps<T>) {
  return (
    <div
      className={
        "bg-surface-1 border border-border-subtle rounded-lg " +
        "overflow-hidden " + className
      }
    >
      {isPending ? (
        <DataTableSkeleton columnCount={columns.length} />
      ) : error !== null ? (
        <DataTableError onRetry={onRetry} />
      ) : !data || data.length === 0 ? (
        <DataTableEmpty message={emptyMessage} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-surface-2">
              <tr className="text-text-tertiary text-caption uppercase tracking-wide">
                {columns.map((col) => (
                  <th
                    key={col.header}
                    scope="col"
                    className={
                      "px-4 py-3 font-medium " +
                      (col.align === "right" ? "text-right" : "")
                    }
                  >
                    {col.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((item) => (
                <tr
                  key={rowKey(item)}
                  className="border-t border-border-subtle"
                >
                  {columns.map((col) => (
                    <td
                      key={col.header}
                      className={
                        "px-4 py-3 text-body-sm " +
                        (col.align === "right" ? "text-right" : "")
                      }
                    >
                      {col.cell(item)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


function DataTableSkeleton({ columnCount }: { columnCount: number }) {
  return (
    <div className="p-6 space-y-3" role="status" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-4">
          {Array.from({ length: columnCount }, (_, c) => (
            <div
              key={c}
              className="h-4 flex-1 rounded-sm bg-surface-2 animate-pulse"
            />
          ))}
        </div>
      ))}
    </div>
  );
}


function DataTableEmpty({ message }: { message: string }) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="text-body text-text-tertiary">{message}</p>
    </div>
  );
}


function DataTableError({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="px-6 py-12 flex flex-col items-center text-center gap-3"
    >
      <AlertTriangle
        size={28}
        strokeWidth={1.5}
        className="text-danger-fg"
        aria-hidden
      />
      <p className="text-body text-text-secondary">
        Couldn't load the list. Try again, or check the broker logs.
      </p>
      {onRetry !== undefined && (
        <button
          type="button"
          onClick={onRetry}
          className={
            "inline-flex items-center gap-2 h-9 px-3 rounded-md " +
            "bg-action-secondary text-action-secondary-text " +
            "text-body-sm font-medium " +
            "hover:opacity-90 " +
            "focus-visible:outline-none focus-visible:shadow-focus"
          }
        >
          <RefreshCw size={14} strokeWidth={2} aria-hidden />
          Try again
        </button>
      )}
    </div>
  );
}
