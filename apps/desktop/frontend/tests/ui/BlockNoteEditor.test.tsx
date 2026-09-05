import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { BlockNoteEditor } from '@blocknote/core';
import { BlockNoteView } from '@blocknote/shadcn';

afterEach(cleanup);

describe('real BlockNote editor', () => {
  it.each(['Order review', '受注内容を確認', '주문 내용을 확인'])('persists and restores %s without conversion loss', async (text) => {
    const editor = BlockNoteEditor.create({ initialContent: [{ type: 'paragraph', content: text }] });
    const view = render(<BlockNoteView editor={editor} />);
    expect(screen.getByText(text)).toBeInTheDocument();
    await act(async () => {
      editor.updateBlock(editor.document[0], { content: `${text} 123` });
    });
    const saved = JSON.parse(JSON.stringify(editor.document));
    view.unmount();
    const restored = BlockNoteEditor.create({ initialContent: saved });
    render(<BlockNoteView editor={restored} />);
    expect(screen.getByText(`${text} 123`)).toBeInTheDocument();
    expect(restored.document).toEqual(saved);
  });

  it('supports undo and redo with one ProseMirror instance', async () => {
    const editor = BlockNoteEditor.create({ initialContent: [{ type: 'paragraph', content: 'Before' }] });
    render(<BlockNoteView editor={editor} />);
    await act(async () => { editor.updateBlock(editor.document[0], { content: 'After' }); });
    expect(screen.getByText('After')).toBeInTheDocument();
    await act(async () => { expect(editor.undo()).toBe(true); });
    expect(screen.getByText('Before')).toBeInTheDocument();
    await act(async () => { expect(editor.redo()).toBe(true); });
    expect(screen.getByText('After')).toBeInTheDocument();
  });

  it('round-trips lists and multilingual tables and strips active HTML', async () => {
    const editor = BlockNoteEditor.create();
    const blocks = editor.tryParseHTMLToBlocks('<ul><li>確認</li></ul><table><tr><td>주문</td><td>Orders</td></tr></table><p onclick="alert(1)">Safe</p><script>alert(1)</script>');
    expect(blocks.some((block) => block.type === 'table')).toBe(true);
    const persisted = JSON.parse(JSON.stringify(blocks));
    const restored = BlockNoteEditor.create({ initialContent: persisted });
    render(<BlockNoteView editor={restored} />);
    expect(screen.getByText('주문')).toBeInTheDocument();
    expect(screen.getByText('確認')).toBeInTheDocument();
    expect(document.querySelector('[onclick]')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
  });

  it('does not replace content during composition or unrelated rerenders', async () => {
    const editor = BlockNoteEditor.create({ initialContent: [{ type: 'paragraph', content: '한국어' }] });
    const view = render(<BlockNoteView editor={editor} />);
    const editable = view.container.querySelector('[contenteditable="true"]')!;
    fireEvent.compositionStart(editable, { data: '한' });
    view.rerender(<BlockNoteView editor={editor} theme="light" />);
    fireEvent.compositionEnd(editable, { data: '한국어' });
    expect(screen.getByText('한국어')).toBeInTheDocument();
    // Native IME keystroke acceptance additionally runs in the packaged Windows WebView.
  });
});
