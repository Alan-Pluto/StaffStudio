import EmployeeAvatar from '@/components/EmployeeAvatar';
import { employeeDisplayName } from '@/employee';

import {
  CHAT_EMPTY_CARD_CLASS,
  CHAT_EMPTY_CLASS,
  CHAT_EMPTY_GREETING_CARD_CLASS,
  CHAT_EMPTY_ROLE_CLASS,
  CHAT_EMPTY_STAT_CELL_CLASS,
  CHAT_EMPTY_SUBTITLE_CLASS,
  CHAT_EMPTY_TAGS_CLASS,
  CHAT_EMPTY_TITLE_CLASS,
} from '../chatPageStyles';
import type { UseChatSession } from '../useChatSession';

export default function ChatEmptyState({ chat }: { chat: UseChatSession }) {
  const { displayedAgent, displayedProfile, emptyRoleSummary, emptyProfileTags, emptyStats } = chat;
  const displayName = displayedAgent ? employeeDisplayName(displayedAgent) : '';
  const displayNameLength = Array.from(displayName).length;
  const greetingFontSize = displayNameLength > 20 ? 20 : displayNameLength > 12 ? 24 : displayNameLength > 6 ? 30 : 36;

  return (
    <div className={CHAT_EMPTY_CLASS}>
      <div className={CHAT_EMPTY_GREETING_CARD_CLASS}>
        <div className="flex min-h-[102px] w-full gap-[10px]">
          <div className="relative h-[102px] w-[136px] shrink-0 self-end">
            <div className="absolute bottom-0 left-0 h-[160px] w-[136px]">
            <EmployeeAvatar
              profile={displayedProfile ?? undefined}
              agent={displayedAgent ?? undefined}
              width={136}
              height={160}
              radius={0}
              fit="cover"
              objectPosition="bottom"
              className="bg-transparent!"
            />
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-col justify-center gap-[8px] py-[12px] capitalize">
            <strong
              className={`${CHAT_EMPTY_TITLE_CLASS} max-w-full [overflow-wrap:anywhere]`}
              style={{ fontSize: `${greetingFontSize}px` }}
              title={displayName}
            >
              Hello 我是{displayName}！
            </strong>
            <span className={CHAT_EMPTY_SUBTITLE_CLASS}>我们来做什么？</span>
          </div>
        </div>
      </div>

      <div className={CHAT_EMPTY_CARD_CLASS}>
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-[8px] px-[4px]">
          <p className={CHAT_EMPTY_ROLE_CLASS}>{emptyRoleSummary}</p>
          <div className={CHAT_EMPTY_TAGS_CLASS}>
            {emptyProfileTags.map((tag, index) => (
              <span key={`${tag}-${index}`}>{tag}</span>
            ))}
          </div>
        </div>
        <div className="flex flex-1 items-stretch">
          {emptyStats.map((item) => (
            <div key={item.label} className={CHAT_EMPTY_STAT_CELL_CLASS}>
              <span className="text-[18px] font-medium leading-none">{item.value}</span>
              <span className="text-[10px] leading-none">{item.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
