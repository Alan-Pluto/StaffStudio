import { describe, expect, it } from 'vitest';

import { getChannelPresentation } from './channelPresentation';

describe('channel presentation', () => {
  it.each([
    ['wechat', '微信', '微信用户', '机器人 ID'],
    ['wecom', '企业微信', '企业微信用户', '机器人 ID'],
    ['feishu', '飞书', '飞书用户', 'App ID'],
  ])('%s uses its actual channel labels', (channel, name, userLabel, identifierLabel) => {
    const presentation = getChannelPresentation(channel);
    expect(presentation.name).toBe(name);
    expect(presentation.userLabel).toBe(userLabel);
    expect(presentation.identifierLabel).toBe(identifierLabel);
    expect(presentation.disconnectDescription).toContain(name);
  });

  it('prefers the configured channel name for newly added channels', () => {
    const presentation = getChannelPresentation('custom', '内部协作');
    expect(presentation.name).toBe('内部协作');
    expect(presentation.userLabel).toBe('内部协作用户');
    expect(presentation.blurb).toBe('通过内部协作与数字员工对话。');
  });
});
