function app() {
  return {
    loggedIn: false,
    loginPassword: '',
    loginError: '',
    loginLoading: false,
    tab: 'overview',
    accounts: [],
    providers: [],
    refreshing: false,
    lastRefresh: '',
    // 表单
    form: { provider: '', display_name: '', api_key: '', config: { base_url: '', api_format: 'openai' } },
    creating: false,
    formError: '',
    // 趋势
    trend: { accountId: '', days: '7', points: [], loading: false, error: '', account: null, chartInstance: null },
    // 河流图（全部窗口型账户已用%堆叠流动）
    stream: { days: '7', keys: [], series: [], loading: false, error: '', loaded: false, hasWindowAccounts: true },
    // 通知配置
    notify: {
      notify_serverchan_key: '', notify_telegram_bot_token: '', notify_telegram_chat_id: '',
      notify_smtp_host: '', notify_smtp_port: '', notify_smtp_user: '', notify_smtp_password: '', notify_smtp_to: '',
      alert_balance_threshold: '', alert_used_threshold: '',
      saving: false, testing: false, msg: '', ok: false, _loaded: null,
    },
    // 模型 tab：按账户分组的实时模型 + 一键刷新
    modelsByAccount: {},      // { [accountId]: { display_name, provider, models, live_error, fetched_at } }
    modelsLoading: false,

    async init() {
      const r = await fetch('/api/session').then(r => r.json());
      this.loggedIn = r.logged_in;
      if (this.loggedIn) { await this.loadAccounts(); }
      // 窗口缩放时重绘河流图与趋势图（防抖）
      let rt;
      window.addEventListener('resize', () => {
        clearTimeout(rt);
        rt = setTimeout(() => {
          if (this.tab === 'trend' && this.stream.series.length) this.renderStream();
          if (this.tab === 'trend' && this.trend.chartInstance) this.renderChart();
        }, 200);
      });
    },
    async login() {
      this.loginError = ''; this.loginLoading = true;
      try {
        const fd = new FormData(); fd.set('password', this.loginPassword);
        const r = await fetch('/api/login', { method: 'POST', body: fd });
        if (!r.ok) { const e = await r.json(); throw new Error(e.detail || '登录失败'); }
        this.loggedIn = true; this.loginPassword = '';
        await this.loadAccounts();
      } catch (e) { this.loginError = e.message; }
      finally { this.loginLoading = false; }
    },
    async logout() {
      await fetch('/api/logout', { method: 'POST' });
      this.loggedIn = false; this.accounts = [];
    },
    async loadAccounts() {
      const r = await fetch('/api/accounts').then(r => r.json());
      this.accounts = r.accounts || [];
      this.updateLastRefresh();
    },
    async loadProviders() {
      if (this.providers.length) return;
      const r = await fetch('/api/providers').then(r => r.json());
      this.providers = r.providers || [];
      if (!this.form.provider && this.providers.length) this.form.provider = this.providers[0].provider;
      await this.loadNotify();
    },
    async loadNotify() {
      try {
        const r = await fetch('/api/notify/config').then(r => r.json());
        this.notify._loaded = r;
        // 非敏感字段直接回填；敏感字段（带 set/masked）不回填明文，让用户决定是否重填
        for (const k of ['alert_balance_threshold','alert_used_threshold','notify_telegram_chat_id',
                          'notify_smtp_host','notify_smtp_port','notify_smtp_user','notify_smtp_to']) {
          if (r[k]) this.notify[k] = r[k];
        }
      } catch (e) { /* 未登录等，忽略 */ }
    },
    async saveNotify() {
      this.notify.saving = true; this.notify.msg = '';
      try {
        const payload = {};
        for (const k of Object.keys(this.notify)) {
          if (['saving','testing','msg','ok','_loaded'].includes(k)) continue;
          if (this.notify[k] !== '') payload[k] = this.notify[k];
        }
        const r = await fetch('/api/notify/config', {
          method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error('保存失败');
        this.notify.msg = '已保存'; this.notify.ok = true;
        setTimeout(() => this.notify.msg = '', 3000);
      } catch (e) { this.notify.msg = e.message; this.notify.ok = false; }
      finally { this.notify.saving = false; }
    },
    async testNotify() {
      this.notify.testing = true; this.notify.msg = '';
      try {
        const r = await fetch('/api/notify/test', { method:'POST' }).then(r => r.json());
        if (r.any_ok) { this.notify.msg = '测试通知已发送，请查收'; this.notify.ok = true; }
        else { this.notify.msg = '发送失败：' + JSON.stringify(r.results); this.notify.ok = false; }
      } catch (e) { this.notify.msg = e.message; this.notify.ok = false; }
      finally { this.notify.testing = false; }
    },
    async loadModels() {
      // 模型表已改为纯动态：进入模型 tab 时不预加载静态表，
      // 改为首次进入时自动拉取各账户的实时模型。
      if (Object.keys(this.modelsByAccount).length === 0 && this.accounts.length) {
        await this.refreshAllModels();
      }
    },
    async refreshAllModels() {
      if (!this.accounts.length) return;
      this.modelsLoading = true;
      try {
        // 并发拉取所有账户的 /v1/models，互不阻塞；以 account.id 为 key 聚合
        const results = await Promise.all(
          this.accounts.map(a =>
            fetch(`/api/accounts/${a.id}/models`)
              .then(r => r.ok ? r.json() : { account_id: a.id, models: [], live_error: `HTTP ${r.status}` })
              .catch(e => ({ account_id: a.id, models: [], live_error: e.message }))
          )
        );
        const byId = {};
        for (const res of results) {
          // 后端返回 account_id；失败兜底对象也已带 account_id，统一用它
          const key = res.account_id;
          if (key !== undefined && key !== null) byId[key] = res;
        }
        // 整体替换触发 Alpine 响应式更新
        this.modelsByAccount = { ...byId };
      } finally { this.modelsLoading = false; }
    },
    async createAccount() {
      this.formError = ''; this.creating = true;
      try {
        // 自定义 API 需带 base_url + api_format；其它 provider 不传 config
        const payload = { provider: this.form.provider, display_name: this.form.display_name, api_key: this.form.api_key };
        if (this.form.provider === 'openai_proxy') {
          const base = (this.form.config?.base_url || '').trim();
          if (!base) throw new Error('请填写 API 站点地址（base_url）');
          payload.config = {
            base_url: base,
            api_format: this.form.config?.api_format || 'openai',
          };
        }
        const r = await fetch('/api/accounts', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify(payload),
        });
        if (!r.ok) { const e = await r.json(); throw new Error(e.detail || '添加失败'); }
        this.form = { provider: this.providers[0]?.provider || '', display_name: '', api_key: '', config: { base_url: '', api_format: 'openai' } };
        await this.loadAccounts();
      } catch (e) { this.formError = e.message; }
      finally { this.creating = false; }
    },
    async deleteAccount(acc) {
      if (!confirm(`确认删除「${acc.display_name}」？`)) return;
      await fetch(`/api/accounts/${acc.id}`, { method: 'DELETE' });
      await this.loadAccounts();
    },
    async toggleEnabled(acc) {
      await fetch(`/api/accounts/${acc.id}`, {
        method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: !acc.enabled }),
      });
      await this.loadAccounts();
    },
    async loadStream() {
      this.stream.loading = true; this.stream.error = '';
      try {
        const r = await fetch(`/api/history?days=${this.stream.days}`).then(r => r.json());
        this.stream.keys = r.keys || [];
        this.stream.series = r.series || [];
        this.stream.hasWindowAccounts = r.has_window_accounts !== false;
        this.stream.loaded = true;
        this.$nextTick(() => this.renderStream());
      } catch (e) { this.stream.error = e.message; }
      finally { this.stream.loading = false; }
    },
    renderStream() {
      const svgEl = this.$refs.streamchart;
      if (!svgEl) return;

      // d3 未加载（CDN 失败）：显示降级提示，不报错
      if (typeof d3 === 'undefined') {
        svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#6B6B66" font-size="13">图表库加载失败，请检查网络后刷新页面</text>';
        return;
      }

      if (!this.stream.series.length) return;

      const keys = this.stream.keys;
      const data = this.stream.series.map(d => ({
        date: new Date(d.date.replace(' ', 'T')),
        ...Object.fromEntries(keys.map(k => [k, +d[k] || 0])),
      }));

      const W = svgEl.clientWidth || 800, H = svgEl.clientHeight || 360;
      const margin = { top: 28, right: 16, bottom: 40, left: 48 };
      const w = W - margin.left - margin.right, h = H - margin.top - margin.bottom;

      d3.select(svgEl).selectAll('*').remove();
      const svg = d3.select(svgEl)
        .attr('viewBox', `0 0 ${W} ${H}`)
        .append('g').attr('transform', `translate(${margin.left},${margin.top})`);

      const x = d3.scaleTime().domain(d3.extent(data, d => d.date)).range([0, w]);
      const y = d3.scaleLinear().range([h, 0]);

      // stack + wiggle（居中流动）
      const stack = d3.stack().keys(keys).offset(d3.stackOffsetWiggle).order(d3.stackOrderInsideOut);
      const series = stack(data);
      y.domain([
        d3.min(series, s => d3.min(s, d => d[0])),
        d3.max(series, s => d3.max(s, d => d[1])),
      ]);

      const palette = ['#0F766E', '#B45309', '#2563EB', '#7C3AED', '#DB2777', '#65A30D', '#0891B2', '#CA8A04'];
      const color = (i) => palette[i % palette.length];

      const area = d3.area()
        .x(d => x(d.data.date))
        .y0(d => y(d[0]))
        .y1(d => y(d[1]))
        .curve(d3.curveCardinal);

      svg.append('g').selectAll('path')
        .data(series)
        .join('path')
        .attr('fill', (_, i) => color(i))
        .attr('fill-opacity', 0.78)
        .attr('stroke', '#FAFAF7')
        .attr('stroke-width', 0.5)
        .attr('d', area)
        .style('cursor', 'pointer')
        .append('title')
        .text((d, i) => `${keys[i]}`);

      // X 轴：根据跨度动态选格式（≤2天显时分，否则日期）
      const spanHours = (data[data.length - 1].date - data[0].date) / 3600000;
      const fmt = spanHours <= 48 ? d3.timeFormat('%m-%d %H:%M') : d3.timeFormat('%m-%d');
      const tickCount = Math.min(8, Math.max(3, Math.floor(w / 90)));
      svg.append('g').attr('transform', `translate(0,${h})`)
        .call(d3.axisBottom(x).ticks(tickCount).tickFormat(fmt))
        .call(g => g.select('.domain').remove())
        .call(g => g.selectAll('.tick line').remove())
        .selectAll('text').attr('fill', '#6B6B66').style('font-family', 'Space Mono, monospace').style('font-size', '10px');

      // 图例（顶部，自动换行）
      const legend = svg.append('g').attr('transform', `translate(0, -16)`);
      let lx = 0;
      keys.forEach((k, i) => {
        const item = legend.append('g').attr('transform', `translate(${lx},0)`);
        item.append('rect').attr('width', 10).attr('height', 10).attr('fill', color(i));
        item.append('text').attr('x', 14).attr('y', 9).attr('fill', '#6B6B66').style('font-size', '11px').text(k);
        lx += 20 + (k.length * 7) + 12;
      });
    },
    async loadTrend() {
      if (!this.trend.accountId) { this.trend.points = []; return; }
      this.trend.loading = true; this.trend.error = '';
      try {
        const r = await fetch(`/api/accounts/${this.trend.accountId}/history?days=${this.trend.days}`).then(r => r.json());
        this.trend.account = r.account;
        this.trend.points = r.points || [];
        this.$nextTick(() => this.renderChart());
      } catch (e) { this.trend.error = e.message; }
      finally { this.trend.loading = false; }
    },
    renderChart() {
      if (!this.$refs.chart) return;
      if (!this.trend.chartInstance) {
        this.trend.chartInstance = echarts.init(this.$refs.chart);
      }
      const acc = this.trend.account;
      const pts = this.trend.points;
      const xData = pts.map(p => p.fetched_at ? new Date(p.fetched_at.replace(' ', 'T')).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '');
      let series = [], yName = '';
      // 翡翠系配色，匹配极简金融风
      const palette = { primary: '#0F766E', ember: '#B45309', brick: '#B91C1C' };
      if (acc && acc.type === 'balance') {
        const cur = (pts.find(p => p.currency) || {}).currency || 'CNY';
        yName = cur === 'USD' ? '余额 (USD)' : '余额 (CNY)';
        series = [{ name: '余额', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
          data: pts.map(p => p.balance), itemStyle: { color: palette.primary },
          lineStyle: { color: palette.primary, width: 1.5 },
          areaStyle: { color: 'rgba(15,118,110,0.08)' } }];
      } else {
        yName = '已用百分比 (%)';
        const hasFive = pts.some(p => p.five_hour_used != null);
        const hasWeekly = pts.some(p => p.weekly_used != null);
        series = [];
        if (hasFive) series.push({ name: '5小时窗口', type: 'line', smooth: true, symbol: 'none',
          data: pts.map(p => p.five_hour_used), itemStyle: { color: palette.primary },
          lineStyle: { color: palette.primary, width: 1.5 } });
        if (hasWeekly) series.push({ name: '每周窗口', type: 'line', smooth: true, symbol: 'none',
          data: pts.map(p => p.weekly_used), itemStyle: { color: palette.ember },
          lineStyle: { color: palette.ember, width: 1.5 } });
      }
      this.trend.chartInstance.setOption({
        tooltip: { trigger: 'axis', backgroundColor: '#17181C', borderColor: '#17181C',
          textStyle: { color: '#FAFAF7', fontFamily: 'Space Mono, monospace' } },
        legend: { top: 0, textStyle: { fontFamily: 'Hanken Grotesk, sans-serif', color: '#6B6B66' } },
        grid: { left: 56, right: 16, top: 40, bottom: 60 },
        xAxis: { type: 'category', data: xData, axisLabel: { rotate: 30, color: '#6B6B66', fontFamily: 'Space Mono, monospace', fontSize: 10 },
          axisLine: { lineStyle: { color: '#E6E4DD' } }, axisTick: { show: false } },
        yAxis: { type: 'value', name: yName, scale: acc.type !== 'balance' ? false : true,
          max: acc.type !== 'balance' ? 100 : null,
          nameTextStyle: { color: '#6B6B66', fontFamily: 'Hanken Grotesk, sans-serif', fontSize: 11 },
          axisLabel: { color: '#6B6B66', fontFamily: 'Space Mono, monospace' },
          splitLine: { lineStyle: { color: '#E6E4DD', type: 'dashed' } },
          axisLine: { show: false }, axisTick: { show: false } },
        series,
      }, true);
    },
    async refresh() {
      this.refreshing = true;
      try {
        const r = await fetch('/api/refresh', { method: 'POST' }).then(r => r.json());
        // 用接口返回的最新结果覆盖前端
        for (const res of (r.results || [])) {
          const acc = this.accounts.find(a => a.id === res.account_id);
          if (acc) acc.latest = res;
        }
        this.lastRefresh = new Date().toLocaleString('zh-CN');
      } catch (e) { alert('刷新失败: ' + e.message); }
      finally { this.refreshing = false; }
    },
    updateLastRefresh() {
      const times = this.accounts.map(a => a.latest?.fetched_at).filter(Boolean);
      if (times.length) this.lastRefresh = new Date(Math.max(...times.map(t => new Date(t)))).toLocaleString('zh-CN');
    },

    // ---- 计算属性 ----
    get errorCount() { return this.accounts.filter(a => a.latest?.raw_error).length; },
    get totalBalanceText() {
      const byCur = {};
      let any = false;
      for (const a of this.accounts) {
        if (a.latest && !a.latest.raw_error && a.latest.type === 'balance' && a.latest.balance != null) {
          const c = a.latest.currency || 'CNY';
          byCur[c] = (byCur[c] || 0) + a.latest.balance; any = true;
        }
      }
      if (!any) return '—';
      return Object.entries(byCur).map(([c, v]) => `${this.currencySymbol(c)}${this.formatBalance(v)}`).join(' + ');
    },

    // ---- 格式化 ----
    providerLabel(p) {
      const m = { deepseek: 'DeepSeek · 余额型', glm: '智谱 GLM Coding Plan', kimi: 'Kimi for Coding', minimax: 'MiniMax Coding Plan', openai_proxy: '自定义 API · OpenAI/Anthropic 兼容' };
      return m[p] || p;
    },
    tierLabel(t) { return { five_hour: '5 小时窗口', weekly: '每周窗口' }[t] || t; },
    currencySymbol(c) { return c === 'USD' ? '$' : c === 'CNY' ? '¥' : ''; },
    formatBalance(v) { return (v ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }); },
    fmtTime(s) { try { return new Date(s).toLocaleString('zh-CN'); } catch { return s; } },
    countdown(s) {
      try {
        const ms = new Date(s).getTime() - Date.now();
        if (ms <= 0) return '即将';
        const h = Math.floor(ms / 3600000);
        const m = Math.floor((ms % 3600000) / 60000);
        if (h >= 24) return `${Math.floor(h/24)}天${h%24}小时`;
        if (h > 0) return `${h}小时${m}分`;
        return `${m}分钟`;
      } catch { return ''; }
    },
    // 模型能力表相关
    providerDisplayName(p) {
      return { deepseek:'DeepSeek', glm:'智谱 GLM', kimi:'Kimi (Moonshot)', minimax:'MiniMax', openai_proxy:'自定义 API' }[p] || p;
    },
    fmtContext(n) {
      if (n === null || n === undefined) return '未公开';
      if (n >= 1000000) return (n/1000000).toFixed(0) + 'M';
      if (n >= 1000) return (n/1000).toFixed(0) + 'K';
      return String(n);
    },
  };
}
