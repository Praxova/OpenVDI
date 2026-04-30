import type { ReactNode } from "react";


interface FormFieldProps {
  /** Visible label text. */
  label: string;
  /** Must match the htmlFor on the actual input child for label
      association. */
  htmlFor: string;
  /** Optional helper text rendered below the input. */
  hint?: string;
  /** Inline error message rendered below the input in red. */
  error?: string;
  /** Marks the field with a visible asterisk. */
  required?: boolean;
  /** The actual input element. Caller is responsible for the id
      (matching htmlFor) and any styling. */
  children: ReactNode;
}


/**
 * Labeled-input wrapper used by admin forms (M4-19 onward). Per FE4.
 *
 * Renders:
 *   <label htmlFor={htmlFor}>label *</label>
 *   {child input}
 *   <p>hint</p>                  (optional, hidden when error is set)
 *   <p role="alert">error</p>    (optional)
 *
 * The input element is the child — caller chooses the type
 * (text, password, checkbox, select, etc.) and styles. This primitive
 * handles only the surrounding label / hint / error chrome.
 */
export function FormField({
  label,
  htmlFor,
  hint,
  error,
  required = false,
  children,
}: FormFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={htmlFor}
        className="text-body-sm font-medium text-text-primary"
      >
        {label}
        {required && (
          <span aria-hidden className="text-danger-fg ml-0.5">
            *
          </span>
        )}
      </label>
      {children}
      {hint !== undefined && error === undefined && (
        <p className="text-caption text-text-tertiary">{hint}</p>
      )}
      {error !== undefined && (
        <p role="alert" className="text-caption text-danger-fg">
          {error}
        </p>
      )}
    </div>
  );
}
