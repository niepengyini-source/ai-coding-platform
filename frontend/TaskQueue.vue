<script setup>
import { nextTick, onMounted, ref, watch } from 'vue';

defineProps(['tasks', 'total', 'pageLabel', 'previous', 'next']);
const key = `platform:task-queue:${document.body.dataset.navigationUser}`;
let initial = window.matchMedia('(max-width: 1180px)').matches;
try {
  const saved = sessionStorage.getItem(key);
  if (saved !== null) initial = saved === 'collapsed';
} catch { /* The task queue works when browser storage is unavailable. */ }
const collapsed = ref(initial);
const list = ref(null);
async function showCurrentTask() {
  await nextTick();
  const selected = list.value?.querySelector('[aria-current=page]');
  if (!collapsed.value && selected) list.value.scrollTop = Math.max(0, selected.offsetTop - 8);
}
onMounted(showCurrentTask);
watch(collapsed, showCurrentTask);
function toggle() {
  collapsed.value = !collapsed.value;
  try { sessionStorage.setItem(key, collapsed.value ? 'collapsed' : 'expanded'); } catch { /* Optional preference. */ }
}
</script>

<template>
  <div class="task-queue" :data-collapsed="collapsed">
    <div class="task-queue-heading">
      <h2 aria-label="我的任务"><span class="task-queue-full-title">我的任务</span><span class="task-queue-short-title" aria-hidden="true">任务</span><span class="task-queue-count">{{ total }}</span></h2>
      <button class="task-queue-toggle" type="button" :aria-label="collapsed ? '展开任务列表' : '收起任务列表'"
        :title="collapsed ? '展开任务列表' : '收起任务列表'" :aria-expanded="!collapsed" aria-controls="task-queue-content" @click="toggle">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /><path :d="collapsed ? 'm13 9 3 3-3 3' : 'm16 9-3 3 3 3'" />
        </svg>
      </button>
    </div>
    <div id="task-queue-content" class="task-queue-content" :hidden="collapsed">
      <nav ref="list" class="task-list" aria-label="我的编码任务列表">
        <a v-for="task in tasks" :key="task.id" :href="task.href" class="task-item" :class="{ selected: task.selected }"
          :aria-current="task.selected ? 'page' : undefined" :data-task-id="task.id">
          <span class="task-number">{{ task.number }}</span>
          <span class="task-item-copy"><span class="task-item-text">{{ task.text }}</span><small>{{ task.status }}</small></span>
        </a>
      </nav>
      <nav class="pagination" aria-label="我的任务分页">
        <a v-if="previous" :href="previous" aria-label="上一页任务">←</a>
        <span>{{ pageLabel }}</span>
        <a v-if="next" :href="next" aria-label="下一页任务">→</a>
      </nav>
    </div>
  </div>
</template>
