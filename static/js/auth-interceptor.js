// 全局 fetch 拦截器：API 请求返回 401 时自动跳转登录页
// 在所有页面的 <head> 最前面引入，确保覆盖所有 fetch 调用
(function () {
  const _origFetch = window.fetch;
  window.fetch = async function (url, opts) {
    const r = await _origFetch(url, opts);
    if (
      r.status === 401 &&
      String(url).includes('/api/') &&
      !String(url).includes('/api/auth/')
    ) {
      window.location.href = '/login';
      throw new Error('未登录，正在跳转登录页...');
    }
    return r;
  };
})();
