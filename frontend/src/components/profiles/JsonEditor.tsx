// ============================================================
// CodeMirror6 JSON editor with live ajv schema-linting.
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Trimmed to the CodeMirror packages we ship; schema comes from core/schema.ts
// (ajv, no Zod). The editor surface stays dark (project convention: --term-bg
// is dark in every theme) via the oneDark theme.
// ============================================================

import { useRef, useEffect, useMemo } from 'react';
import { EditorState } from '@codemirror/state';
import {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, dropCursor, highlightSpecialChars,
} from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { indentOnInput, syntaxHighlighting, defaultHighlightStyle, bracketMatching, foldGutter, foldKeymap } from '@codemirror/language';
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { linter, lintGutter, lintKeymap } from '@codemirror/lint';
import { json } from '@codemirror/lang-json';
import { oneDark } from '@codemirror/theme-one-dark';
import Ajv from 'ajv';
import { schemaForMode, type SchemaMode } from './core/schema';

interface JsonEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  schemaMode?: SchemaMode;
}

export function JsonEditor({ value, onChange, readOnly = false, schemaMode = 'full' }: JsonEditorProps) {
  const parent = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  const schemaLinter = useMemo(() => {
    const ajv = new Ajv({ allErrors: true, strict: false, allowUnionTypes: true });
    const validate = ajv.compile(schemaForMode(schemaMode));
    return linter((view) => {
      const doc = view.state.doc.toString();
      if (!doc.trim()) return [];
      const diagnostics: { from: number; to: number; severity: 'error'; message: string }[] = [];
      try {
        const parsed = JSON.parse(doc);
        if (!validate(parsed) && validate.errors) {
          for (const err of validate.errors) {
            diagnostics.push({
              from: 0, to: view.state.doc.length, severity: 'error',
              message: `Схема: ${err.instancePath || '/'} ${err.message}`,
            });
          }
        }
      } catch (e) {
        diagnostics.push({ from: 0, to: view.state.doc.length, severity: 'error', message: (e as Error).message || 'Некорректный JSON' });
      }
      return diagnostics;
    });
  }, [schemaMode]);

  useEffect(() => {
    if (!parent.current) return;
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(), foldGutter(),
        drawSelection(), dropCursor(), EditorState.allowMultipleSelections.of(true), indentOnInput(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }), bracketMatching(), closeBrackets(),
        highlightActiveLine(),
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...foldKeymap, ...lintKeymap, indentWithTab]),
        json(), lintGutter(), schemaLinter, oneDark,
        EditorView.updateListener.of((u) => { if (u.docChanged) onChange(u.state.doc.toString()); }),
        EditorView.editable.of(!readOnly),
        EditorState.readOnly.of(readOnly),
        EditorView.theme({
          '&': { height: '100%', fontSize: '13px', backgroundColor: 'var(--term-bg)' },
          '&.cm-focused': { outline: 'none' },
          '.cm-scroller': { fontFamily: 'var(--mono)', overflow: 'auto' },
          '.cm-gutters': { backgroundColor: 'var(--term-bg)', border: 'none' },
        }),
      ],
    });
    const view = new EditorView({ state, parent: parent.current });
    viewRef.current = view;
    return () => view.destroy();
    // Recreate when the schema/readonly changes (linter is baked into extensions).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schemaMode, readOnly]);

  // Push external value changes into the view without losing cursor on typing.
  useEffect(() => {
    const view = viewRef.current;
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    }
  }, [value]);

  return (
    <div ref={parent} style={{
      height: '100%', width: '100%', overflow: 'hidden',
      border: '1px solid var(--line)', borderRadius: 'var(--r-md)', background: 'var(--term-bg)',
    }} />
  );
}
