export interface Shop {
  id: number;
  domain: string;
  name: string;
  ownerEmail: string;
  currency: string;
  ianaTimezone: string;
  accessToken: string;
}

export interface LineItem {
  productId: string;
  quantity: number;
}

export interface Order {
  id: string;
  totalPrice: string;
  lineItems: LineItem[];
}

export interface Product {
  id: string;
  title: string;
}

export interface ListOrdersParams {
  createdAtMin: Date;
  createdAtMax: Date;
  financialStatus?: "paid" | "any";
}

const API_VERSION = "2025-07";
const PAGE_SIZE = 250;

/** Error thrown for any non-2xx response from the Shopify Admin API. */
export class ShopifyApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterMs?: number,
  ) {
    super(message);
    this.name = "ShopifyApiError";
  }
}

interface RawLineItem {
  product_id: number | null;
  quantity: number;
}

interface RawOrder {
  id: number;
  total_price: string;
  line_items: RawLineItem[];
}

interface RawProduct {
  id: number;
  title: string;
}

/**
 * Cliente mínimo da Shopify Admin REST API, ligado a uma loja.
 *
 * Não faz retries. Respostas não 2xx, incluindo 429, lançam ShopifyApiError
 * (que carrega retryAfterMs quando a Shopify o indica).
 */
export class ShopifyClient {
  private readonly baseUrl: string;
  private readonly accessToken: string;

  constructor(shop: Shop) {
    this.baseUrl = `https://${shop.domain}/admin/api/${API_VERSION}`;
    this.accessToken = shop.accessToken;
  }

  /** Lists orders created within the window. Trata a paginação (cabeçalho Link / page_info). */
  async listOrders(params: ListOrdersParams): Promise<Order[]> {
    const query = new URLSearchParams({
      limit: String(PAGE_SIZE),
      status: "any",
      financial_status: params.financialStatus ?? "any",
      created_at_min: params.createdAtMin.toISOString(),
      created_at_max: params.createdAtMax.toISOString(),
    });

    const orders: Order[] = [];
    let url: string | null = `${this.baseUrl}/orders.json?${query.toString()}`;
    while (url) {
      const page: { body: { orders: RawOrder[] }; nextUrl: string | null } = await this.request(url);
      orders.push(...page.body.orders.map(toOrder));
      url = page.nextUrl;
    }
    return orders;
  }

  /** Fetches one product by id. */
  async getProduct(productId: string): Promise<Product> {
    const { body } = await this.request<{ product: RawProduct }>(`${this.baseUrl}/products/${productId}.json`);
    return { id: String(body.product.id), title: body.product.title };
  }

  private async request<T>(url: string): Promise<{ body: T; nextUrl: string | null }> {
    const response = await fetch(url, {
      headers: {
        "X-Shopify-Access-Token": this.accessToken,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      const retryAfterHeader = response.headers.get("retry-after");
      const retryAfterMs = retryAfterHeader ? Number(retryAfterHeader) * 1000 : undefined;
      throw new ShopifyApiError(
        response.status,
        `Shopify API responded ${response.status} for ${new URL(url).pathname}`,
        retryAfterMs,
      );
    }
    const body = (await response.json()) as T;
    return { body, nextUrl: parseNextLink(response.headers.get("link")) };
  }
}

function toOrder(raw: RawOrder): Order {
  return {
    id: String(raw.id),
    totalPrice: raw.total_price,
    lineItems: raw.line_items
      .filter((item) => item.product_id !== null)
      .map((item) => ({ productId: String(item.product_id), quantity: item.quantity })),
  };
}

/** Extracts the rel="next" URL from a Shopify Link header, if present. */
function parseNextLink(header: string | null): string | null {
  if (!header) return null;
  for (const part of header.split(",")) {
    const match = /<([^>]+)>;\s*rel="next"/.exec(part);
    if (match?.[1]) return match[1];
  }
  return null;
}
