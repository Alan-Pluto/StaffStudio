// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ScheduledTaskDraftRead } from '@/types';

import ScheduledDraftCard from './ScheduledDraftCard';

const draft: ScheduledTaskDraftRead = {
  should_create: true,
  tenant_id: 'tenant-demo',
  agent_id: 'agent-demo',
  title: 'Daily price check',
  prompt: 'Check and summarize the A1 price',
  description: 'Remains actionable after refresh',
  schedule_type: 'daily',
  schedule: { time: '09:00' },
  timezone: 'Asia/Shanghai',
  confidence: 1,
};

afterEach(cleanup);

describe('ScheduledDraftCard actions', () => {
  it('confirms the complete persisted draft', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ScheduledDraftCard draft={draft} onConfirm={onConfirm} onDismiss={vi.fn()} />,
    );

    expect(screen.getByText('Daily price check')).toBeTruthy();
    expect(screen.getByText('Check and summarize the A1 price')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '确认创建' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(draft);
  });

  it('dismisses without confirming', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onDismiss = vi.fn();
    render(
      <ScheduledDraftCard draft={draft} onConfirm={onConfirm} onDismiss={onDismiss} />,
    );

    await user.click(screen.getByRole('button', { name: '忽略' }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
