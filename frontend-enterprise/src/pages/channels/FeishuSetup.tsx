import { useState } from 'react';
import { notify } from '@/components/ui/app-toast';

import { Input } from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';

import { api, TENANT_ID } from '../../api/client';
import type { ChannelBindingRead } from '../../types';
import { StatusBadge } from '../scheduled-tasks/StatusBadge';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-5 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

export default function FeishuSetup({
  binding,
  onChanged,
}: {
  binding: ChannelBindingRead;
  onChanged: (updated: ChannelBindingRead) => void;
}) {
  const configuredAppId = binding.app_id || '';
  const [editing, setEditing] = useState(!configuredAppId);
  const [appId, setAppId] = useState(configuredAppId);
  const [appSecret, setAppSecret] = useState('');
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!appId.trim() || !appSecret.trim()) {
      notify.error('请填写完整凭证');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.post<ChannelBindingRead>(
        `/api/enterprise/channels/${binding.id}/feishu/credentials`,
        {
          tenant_id: TENANT_ID,
          app_id: appId.trim(),
          app_secret: appSecret.trim(),
        },
      );
      setAppSecret('');
      setEditing(false);
      onChanged(updated);
      notify.success('已保存');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存凭证失败');
    } finally {
      setSaving(false);
    }
  }

  if (configuredAppId && !editing) {
    return (
      <div className="flex flex-wrap items-center gap-[10px] rounded-[10px] bg-[#fafbfc] p-[16px]">
        <span className="text-[12px] text-[#464c5e]">凭证已配置</span>
        <span className="text-[12px] text-[#858b9c]">App ID：{configuredAppId}</span>
        {binding.bot_name && (
          <span className="text-[12px] text-[#858b9c]">机器人：{binding.bot_name}</span>
        )}
        {binding.provider_tenant_key && (
          <span className="text-[12px] text-[#858b9c]">
            Tenant：{binding.provider_tenant_key}
          </span>
        )}
        <StatusBadge tone={binding.connected ? 'green' : 'gray'}>
          {binding.connected ? '已连接' : '未连接'}
        </StatusBadge>
        <UIButton
          variant="outline"
          onClick={() => {
            setAppId(configuredAppId);
            setAppSecret('');
            setEditing(true);
          }}
          className={OUTLINE_BUTTON_CLASS}
        >
          轮换 Secret
        </UIButton>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-[12px] rounded-[10px] bg-[#fafbfc] p-[16px]">
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        App ID
        <Input
          type="text"
          value={appId}
          disabled={Boolean(configuredAppId)}
          autoComplete="off"
          data-1p-ignore="true"
          data-lpignore="true"
          onChange={(event) => setAppId(event.target.value)}
          className="h-8 rounded-[10px] text-[12px]"
        />
      </label>
      <label className="flex flex-col gap-[6px] text-[12px] text-[#464c5e]">
        App Secret
        <Input
          type="password"
          value={appSecret}
          autoComplete="off"
          name="feishu-app-secret-no-password-manager"
          data-1p-ignore="true"
          data-lpignore="true"
          onChange={(event) => setAppSecret(event.target.value)}
          className="h-8 rounded-[10px] text-[12px]"
        />
      </label>
      <div className="flex justify-end gap-[8px]">
        {configuredAppId && (
          <UIButton
            variant="outline"
            onClick={() => setEditing(false)}
            className={OUTLINE_BUTTON_CLASS}
          >
            取消
          </UIButton>
        )}
        <UIButton onClick={() => void save()} disabled={saving} className={PRIMARY_BUTTON_CLASS}>
          保存
        </UIButton>
      </div>
    </div>
  );
}
