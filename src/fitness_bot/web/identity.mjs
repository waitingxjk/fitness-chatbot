// Tokens live only in this page's memory. Never put credentials in image URLs.
export class IdentitySession {
  #token = '';
  #controller = new AbortController();
  #images = new Map();
  #fetcher;
  #objectUrls;
  userId = 'local-user';
  version = 0;
  constructor({fetcher = fetch, objectUrls = URL} = {}) {
    this.#fetcher = fetcher;
    this.#objectUrls = objectUrls;
  }
  get conversationKey() { return `fitness-playground-conversation:${this.userId}`; }
  setIdentity(userId, token = '') {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(userId) || /[\r\n]/.test(userId)) throw new Error('用户 ID 格式无效');
    if (token && (!/^[A-Za-z0-9._~-]+$/.test(token) || /[\r\n]/.test(token))) throw new Error('令牌格式无效');
    this.dispose();
    this.#controller = new AbortController();
    this.userId = userId;
    this.#token = token;
  }
  adoptServerUser(userId) {
    if (!/^[0-9a-f]{32}$/.test(userId) || userId.length !== 32) throw new Error('服务器内部用户 ID 无效');
    if (/^[0-9a-f]{32}$/.test(this.userId) && this.userId !== userId) throw new Error('服务器返回的用户身份不匹配');
    if (this.userId !== userId) this.setIdentity(userId, this.#token);
  }
  dispose() {
    this.version++;
    this.#controller.abort();
    for (const url of this.#images.values()) this.#objectUrls.revokeObjectURL(url);
    this.#images.clear();
  }
  async fetch(path, options = {}) {
    // Only known local API paths may receive credentials; disallow redirects too.
    if (typeof path !== 'string' || !/^\/(?:v1\/|health(?:\?|$))/.test(path) || /[\\\r\n]/.test(path)) throw new Error('仅允许当前服务的 API 地址');
    const headers = new Headers(options.headers);
    headers.set('X-User-Id', this.userId);
    headers.delete('Authorization');
    if (this.#token) headers.set('Authorization', `Bearer ${this.#token}`);
    const version = this.version;
    const signal = options.signal ? AbortSignal.any([options.signal, this.#controller.signal]) : this.#controller.signal;
    const response = await (0, this.#fetcher)(path, {...options, headers, signal, redirect:'error'});
    if (version !== this.version) throw new DOMException('用户已切换', 'AbortError');
    return response;
  }
  async imageUrl(path) {
    if (this.#images.has(path)) return this.#images.get(path);
    const version = this.version;
    const response = await this.fetch(path);
    if (!response.ok) throw new Error(`图片读取失败（${response.status}）`);
    const blob = await response.blob();
    if (version !== this.version) throw new DOMException('用户已切换', 'AbortError');
    if (!this.#images.has(path)) this.#images.set(path, this.#objectUrls.createObjectURL(blob));
    return this.#images.get(path);
  }
}
