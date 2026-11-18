import { createApp } from 'vue';
import TaskQueue from './TaskQueue.vue';
import './workbench.js';

const root = document.querySelector('[data-task-queue-app]');
const fallback = document.querySelector('[data-task-queue-fallback]');
if (root && fallback) {
  // Use the server's permission-filtered links; ordinary navigation keeps the save guard.
  const tasks = [...fallback.querySelectorAll('.task-item')].map(link => ({
    id: link.dataset.taskId,
    number: link.querySelector('.task-number').textContent,
    text: link.querySelector('[data-task-text]').textContent,
    status: link.querySelector('[data-task-status]').textContent,
    selected: link.classList.contains('selected'),
    href: link.getAttribute('href'),
  }));
  createApp(TaskQueue, {
    tasks,
    total: fallback.dataset.total,
    pageLabel: fallback.querySelector('[data-task-page]').textContent,
    previous: fallback.querySelector('[data-task-previous]')?.getAttribute('href') || '',
    next: fallback.querySelector('[data-task-next]')?.getAttribute('href') || '',
  }).mount(root);
  fallback.hidden = true;
  root.hidden = false;
}
