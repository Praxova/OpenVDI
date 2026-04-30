import { useState, type FormEvent } from "react";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";

import {
  useGrantEntitlementMutation,
  usePoolEntitlementsQuery,
  useRevokeEntitlementMutation,
} from "@/api/admin/entitlements";
import { brokerErrorCode } from "@/api/errors";
import { FormField } from "@/components/admin/FormField";
import type { EntitlementRead, PrincipalType } from "@/types/admin";


interface EntitlementsPanelProps {
  poolId: string;
}


export function EntitlementsPanel({ poolId }: EntitlementsPanelProps) {
  const list = usePoolEntitlementsQuery(poolId);
  const grant = useGrantEntitlementMutation(poolId);
  const revoke = useRevokeEntitlementMutation(poolId);

  const [type, setType] = useState<PrincipalType>("group");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (grant.isPending) return;
    setError(null);

    // Per the M4-05 forward note, lowercase-coerce usernames so
    // entitlements match what JWT subjects carry. Group names stay
    // case-preserved (LDAP DN attributes are case-preserved).
    const trimmed = name.trim();
    const principal_name =
      type === "user" ? trimmed.toLowerCase() : trimmed;

    try {
      await grant.mutateAsync({ principal_type: type, principal_name });
      setName(""); // clear on success; type stays for bulk-grant
    } catch (exc) {
      switch (brokerErrorCode(exc)) {
        case "CONFLICT":
          setError(`${trimmed} is already entitled to this pool.`);
          break;
        case "INVALID_REQUEST":
          setError("Invalid principal name.");
          break;
        default:
          setError("Failed to grant entitlement.");
      }
    }
  };

  const handleRevoke = async (entitlement: EntitlementRead) => {
    const ok = window.confirm(
      `Revoke ${entitlement.principal_type} "${entitlement.principal_name}" ` +
        "from this pool?",
    );
    if (!ok) return;
    setError(null);
    try {
      await revoke.mutateAsync(entitlement.id);
    } catch {
      setError(`Failed to revoke ${entitlement.principal_name}.`);
    }
  };

  return (
    <section
      aria-labelledby="entitlements-heading"
      className={
        "bg-surface-1 border border-border-subtle rounded-lg p-6 " +
        "flex flex-col gap-4"
      }
    >
      <header>
        <h2
          id="entitlements-heading"
          className="font-body text-h3 font-semibold text-text-primary"
        >
          Entitlements
        </h2>
        <p className="text-body-sm text-text-secondary mt-1">
          AD users and groups allowed to connect to this pool.
        </p>
      </header>

      <form onSubmit={handleAdd} className="flex items-end gap-3 flex-wrap">
        <FormField label="Type" htmlFor="ent-type">
          <select
            id="ent-type"
            value={type}
            onChange={(e) => setType(e.target.value as PrincipalType)}
            className={inputClass}
          >
            <option value="group">Group</option>
            <option value="user">User</option>
          </select>
        </FormField>
        <FormField
          label={type === "group" ? "Group name" : "Username"}
          htmlFor="ent-name"
          required
        >
          <input
            id="ent-name"
            type="text"
            required
            maxLength={256}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
          />
        </FormField>
        <button
          type="submit"
          disabled={grant.isPending || name.trim() === ""}
          className={primaryBtn}
        >
          <Plus size={14} aria-hidden />
          Add
        </button>
      </form>

      {error !== null && (
        <div role="alert" className={alertBanner}>
          <AlertTriangle size={14} className="inline mr-1" aria-hidden />
          {error}
        </div>
      )}

      {list.isPending && (
        <p className="text-text-tertiary text-body-sm">Loading…</p>
      )}
      {list.error !== null && (
        <p role="alert" className="text-danger-fg text-body-sm">
          Couldn't load entitlements.
        </p>
      )}
      {list.data !== undefined && list.data.length === 0 && (
        <p className="text-text-tertiary text-body-sm">
          No entitlements yet. Add a group or user above.
        </p>
      )}
      {list.data !== undefined && list.data.length > 0 && (
        <ul className="divide-y divide-border-subtle">
          {list.data.map((ent) => (
            <li
              key={ent.id}
              className="py-2 flex items-center justify-between gap-3"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-text-tertiary text-caption uppercase tracking-wide">
                  {ent.principal_type}
                </span>
                <span className="font-mono text-text-primary truncate">
                  {ent.principal_name}
                </span>
              </div>
              <button
                type="button"
                onClick={() => handleRevoke(ent)}
                aria-label={`Revoke ${ent.principal_name}`}
                disabled={revoke.isPending}
                className={iconBtnDanger}
              >
                <Trash2 size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


// Style constants — duplicated from sibling pages.
const inputClass =
  "h-10 px-3 rounded-md bg-bg border border-border-subtle " +
  "text-text-primary text-body " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const primaryBtn =
  "inline-flex items-center gap-1 h-10 px-4 rounded-md " +
  "bg-action-primary text-text-on-accent text-body font-medium " +
  "hover:bg-action-primary-hover " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const iconBtnDanger =
  "inline-flex items-center justify-center h-8 w-8 rounded-md " +
  "text-text-secondary hover:bg-danger-bg hover:text-danger-fg " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const alertBanner =
  "px-3 py-2 rounded-md text-body-sm " +
  "bg-danger-bg border border-danger-border text-danger-fg";
