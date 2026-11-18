// Run after workbench_fixture.py on the isolated port 8001 server; requires Playwright and Chrome.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

(async () => {
  const c = JSON.parse(fs.readFileSync('runtime/workbench-ui.json', 'utf8'));
  const base = 'http://127.0.0.1:8001';
  const output = 'runtime/workbench-polish';
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  const checks = [], errors = [];
  const check = (name, condition=true) => {assert.ok(condition, name); checks.push(name);};
  const url = index => `${base}/r/${c.round}/?a=${c.tasks[index]}`;
  let page;
  try {
    const context = await browser.newContext({viewport:{width:1440,height:900}});
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const open = async index => {
      await page.goto(url(index));
      await page.locator('[data-task-queue-app] .task-queue').waitFor();
    };
    await page.goto(base + '/login/');
    await page.locator('[name=username]').fill(c.username);
    await page.locator('[name=password]').fill(c.password);
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button',{name:'登录',exact:true}).click()]);
    await open(4);
    check('One compact header leaves the main work area at the top', (await page.locator('.workbench-grid').boundingBox()).y <= 90 && await page.locator('.page-return').count()===0);
    await page.getByLabel('工作台工具',{exact:true}).click();
    check('Secondary actions are grouped behind a named tool icon', await page.getByRole('button',{name:'显示设置',exact:true}).isVisible() && await page.getByRole('link',{name:'我的编码历史',exact:true}).isVisible());
    await page.keyboard.press('Escape');
    check('Escape closes tools and restores the trigger', await page.locator('[data-workbench-menu][open]').count()===0 && await page.getByLabel('工作台工具',{exact:true}).evaluate(e=>e===document.activeElement));
    await page.getByLabel('工作台工具',{exact:true}).click();
    await page.locator('.material-panel').click({position:{x:10,y:10}});
    check('Clicking outside closes tools', await page.locator('[data-workbench-menu][open]').count()===0);
    check('Vue renders only the current page of real tasks', await page.locator('[data-task-queue-app] .task-item').count() === 50);
    check('HTML fallback is hidden only after mounting', await page.locator('[data-task-queue-fallback]').isHidden());
    check('Navigation starts as an icon rail with named links', await page.locator('body').evaluate(e=>e.classList.contains('nav-compact')) && await page.getByRole('link',{name:'我的项目',exact:true}).isVisible());
    await page.getByRole('link',{name:'我的项目',exact:true}).focus();
    await page.getByRole('tooltip').waitFor();
    check('Icon navigation explains itself on keyboard focus', await page.getByRole('tooltip').textContent()==='我的项目');
    await page.getByRole('button',{name:'展开侧边导航',exact:true}).click();
    check('Expanded navigation retains names and existing links', await page.locator('.nav-text').filter({hasText:'我的项目'}).isVisible());
    await page.getByRole('button',{name:'收起侧边导航',exact:true}).click();
    check('Source metadata and settings are outside the main reading area', await page.locator('#workbench-source').isHidden() && await page.locator('#source-information-settings').isHidden());
    await page.getByRole('button',{name:'来源与附加信息',exact:true}).click();
    check('Original metadata stays available in the drawer', (await page.locator('#workbench-source').textContent()).includes('message_id'));
    await page.getByRole('button',{name:'关闭来源与附加信息',exact:true}).click();
    await page.getByLabel('工作台工具',{exact:true}).click();
    await page.getByRole('button',{name:'显示设置',exact:true}).click();
    check('Display mapping form retains its original server action', (await page.locator('#source-information-settings form').getAttribute('action')).endsWith('/display/'));
    await page.keyboard.press('Escape');
    await page.getByLabel('工作台工具',{exact:true}).click();
    await page.getByRole('button',{name:'任务管理',exact:true}).click();
    await page.locator('#submission-controls summary').click();
    await page.locator('#submitted-unit').fill(String(c.units[0]));
    await page.getByRole('button',{name:'查找已提交任务',exact:true}).click();
    await page.locator('.workbench-dialog[open] #submission-controls').waitFor();
    check('Submitted-task filtering reopens management and keeps its results', await page.locator('#submission-controls tbody tr').count()===1);
    await page.getByRole('button',{name:'关闭任务管理',exact:true}).click();
    await open(4);
    const help = page.getByLabel('查看 ASK 的规则', {exact:true});
    await help.hover();
    await page.locator('[data-code-popover]:popover-open').waitFor();
    check('Code explanations are complete on hover without filling each option', (await page.locator('[data-code-popover]').textContent()).includes('surrounding conversation'));
    await help.click();
    await page.mouse.move(100,100);
    check('Clicking pins the rule so it remains readable', await page.locator('[data-code-popover]').isVisible());
    await page.keyboard.press('Escape');
    const original = await page.locator('#current-unit-text').textContent();
    const width = (await page.locator('.material-panel').boundingBox()).width;
    await page.getByRole('button',{name:'收起任务列表',exact:true}).click();
    check('Collapse gives reading space without replacing the selected task',
      (await page.locator('.material-panel').boundingBox()).width > width && await page.locator('#current-unit-text').textContent() === original);
    await page.reload();
    await page.getByRole('button',{name:'展开任务列表',exact:true}).waitFor();
    check('Collapse preference survives navigation');
    await page.getByRole('button',{name:'展开任务列表',exact:true}).click();
    await page.locator(`[data-task-queue-app] [data-task-id="${c.tasks[5]}"]`).click();
    await page.waitForURL(new RegExp(`a=${c.tasks[5]}&page=1`));
    check('Queue navigation synchronizes source and save endpoint',
      await page.locator('#current-unit-text').textContent() !== original &&
      await page.locator('.annotation-form').getAttribute('data-endpoint') === `/api/assignments/${c.tasks[5]}/save/`);
    await page.getByRole('link',{name:'下一页任务',exact:true}).click();
    await page.waitForURL(/page=2/);
    check('Pagination selects a task from that page', await page.locator('[data-task-queue-app] .task-item').count() === 6 &&
      await page.locator('[data-task-queue-app] [aria-current=page]').count() === 1);

    const fallback = await browser.newContext({javaScriptEnabled:false, viewport:{width:1440,height:900}});
    await fallback.addCookies(await context.cookies());
    const fallbackPage = await fallback.newPage();
    await fallbackPage.goto(url(4));
    check('Tasks and ordinary links remain usable without Vue', await fallbackPage.locator('[data-task-queue-fallback] .task-item').first().isVisible());
    await fallback.close();

    await open(0);
    check('Submitted coding stays read-only', await page.locator('[name=primary]').isDisabled() && await page.locator('[data-action=submit_next]').count() === 0);
    await open(4);
    await page.locator(`[data-code-choice="${c.codes[0]}"]`).click();
    await page.locator('[name=note]').fill('根据原文提出的分工，选择计划与分工。');
    await page.waitForFunction(()=>document.querySelector('.save-status').classList.contains('success'));
    await page.reload();
    await page.locator('[data-task-queue-app] .task-queue').waitFor();
    check('Draft selection and note survive reload', await page.locator('[name=primary]').inputValue() === String(c.codes[0]) &&
      (await page.locator('[name=note]').inputValue()).includes('根据原文'));
    const contextBefore = await page.locator('[data-context-after] .context-row').count();
    await page.getByRole('tab',{name:'前后上下文',exact:true}).click();
    check('Context view includes the highlighted current statement', await page.locator('#reading-context .context-row.current').isVisible());
    await page.locator('[data-context-more=after]').click();
    await page.waitForFunction(n=>document.querySelectorAll('[data-context-after] .context-row').length>n,contextBefore);
    check('Additional context still loads');
    await page.getByRole('tab',{name:'当前单元',exact:true}).click();
    await page.evaluate(()=>{document.querySelector('.material-scroll').scrollTop=0;document.querySelector('.annotation-fields').scrollTop=0;window.scrollTo(0,0)});
    await page.screenshot({path:output+'/desktop.png'});
    await page.getByRole('button',{name:'收起任务列表',exact:true}).click();
    await page.screenshot({path:output+'/collapsed.png'});
    await page.getByRole('button',{name:'展开任务列表',exact:true}).click();

    const footerBefore = await page.locator('.annotation-footer').boundingBox();
    await page.locator('.annotation-fields').hover();
    await page.mouse.wheel(0,1800);
    await page.waitForFunction(()=>document.querySelector('.annotation-fields').scrollTop>0);
    const footerAfter = await page.locator('.annotation-footer').boundingBox();
    check('Coding scroll keeps actions fixed and leaves source still', Math.abs(footerBefore.y-footerAfter.y)<1 &&
      await page.locator('.material-scroll').evaluate(e=>e.scrollTop) === 0);

    for (const [w,h] of [[1440,900],[1366,768],[1024,768],[820,1180],[768,1024],[390,844],[320,720]]) {
      await page.setViewportSize({width:w,height:h});
      await page.locator('[data-action=submit_next]').scrollIntoViewIfNeeded();
      const bounds = await page.locator('[data-action=submit_next]').boundingBox();
      check(`No horizontal overflow; confirmation stays reachable at ${w}px`,
        await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1) && bounds.x>=0 && bounds.x+bounds.width<=w+1);
      if (w===390) await page.screenshot({path:output+'/mobile-coding.png'});
    }
    await page.setViewportSize({width:1440,height:900});
    await page.locator('[data-action=submit_next]').click();
    await page.waitForURL(url(5));
    check('Confirmation saves and advances to the next real assignment');

    await page.keyboard.press('Alt+q');
    await page.waitForFunction(()=>document.querySelector('.annotation-form').dataset.quickMode==='true');
    await page.getByLabel('工作台工具',{exact:true}).click();
    const beforeMenu = await page.locator('[name=primary]').inputValue();
    await page.keyboard.press('Alt+Enter');
    await page.keyboard.press('s');
    check('Open tools leave coding shortcuts inactive without ending quick mode', await page.locator('[name=primary]').inputValue()===beforeMenu && await page.locator('[data-shortcut-skip-prompt]').isHidden() && await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    await page.getByRole('button',{name:'显示设置',exact:true}).click();
    const beforeSettings = await page.locator('[name=primary]').inputValue();
    await page.keyboard.press('Alt+Enter');
    await page.keyboard.press('s');
    check('Settings do not let quick shortcuts change the coding behind them', await page.locator('[name=primary]').inputValue()===beforeSettings && await page.locator('[data-shortcut-skip-prompt]').isHidden());
    await page.getByRole('button',{name:'关闭显示设置',exact:true}).click();
    check('Closing settings retains quick coding mode', await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    await page.getByRole('button',{name:'收起代码选择',exact:true}).click();
    check('Hiding the floating picker leaves quick mode available for mouse work', await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    await page.locator(`[data-code-choice="${c.codes[0]}"]`).click();
    await page.getByRole('button',{name:'收起任务列表',exact:true}).click();
    await page.keyboard.press('s');
    check('Mouse selection and queue collapse keep quick mode active', await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    await page.keyboard.press('Enter');
    check('Keyboard still selects after using the mouse', Boolean(await page.locator('[name=primary]').inputValue()));
    await page.getByRole('button',{name:'展开任务列表',exact:true}).click();
    await page.locator('[name=note]').fill('离开前保留的虚构备注');
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator(`[data-task-queue-app] [data-task-id="${c.tasks[6]}"]`).click();
    check('Canceling leave retains the current coding task', page.url()===url(5));
    await page.locator('[name=note]').fill('确认保存后再切换任务');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator(`[data-task-queue-app] [data-task-id="${c.tasks[6]}"]`).click();
    await page.waitForURL(new RegExp(`a=${c.tasks[6]}`));
    await open(5);
    check('Vue task links preserve the existing save-before-leaving guard', await page.locator('[name=note]').inputValue()==='确认保存后再切换任务');
    await page.keyboard.press('Alt+x');
    await open(8);
    await page.keyboard.press('Alt+q');
    await page.locator('[data-split-preview]').click();
    await page.locator('#current-unit-text').evaluate(element=>{
      const range=document.createRange(); range.setStart(element.firstChild,0); range.setEnd(element.firstChild,9);
      const selection=getSelection(); selection.removeAllRanges(); selection.addRange(range);
      element.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    });
    await page.locator('[data-split-confirm]').click();
    await page.waitForURL(value=>value.href!==url(8));
    await page.locator('[data-unsplit-confirm]').waitFor();
    check('Splitting remains available in quick mode and keeps that mode', await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('[data-unsplit-confirm]').click();
    await page.waitForURL(url(8));
    check('Undo split restores the original statement', await page.locator('#current-unit-text').textContent()===original);
    await page.keyboard.press('Alt+Enter');
    await page.locator('[data-shortcut-skip-prompt]').waitFor();
    check('Skip reminder remains visible inside the scrolling editor', await page.locator('[name=note]').evaluate(element=>{
      const box=element.getBoundingClientRect(), panel=element.closest('.annotation-fields').getBoundingClientRect();
      return box.top>=panel.top && box.bottom<=panel.bottom;
    }));
    await page.keyboard.press('Alt+Enter');
    await page.waitForURL(url(9));
    check('Second skip shortcut advances and preserves quick mode', await page.locator('.annotation-form').getAttribute('data-quick-mode')==='true');
    await page.keyboard.press('Alt+x');
    check('Only the exit shortcut ends quick mode', await page.locator('.annotation-form').getAttribute('data-quick-mode')!=='true');
    check('No uncaught browser errors', errors.length===0);
    fs.writeFileSync(output+'/browser-result.json',JSON.stringify({checks,errors},null,2));
    console.log(JSON.stringify({passed:checks.length,checks,errors}));
  } catch (error) {
    if (page) await page.screenshot({path:output+'/failure.png',fullPage:true}).catch(()=>{});
    console.error(JSON.stringify({completed:checks,errors}));
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
