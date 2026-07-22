/**
 * ngx_http_upstream_module — biến nhúng (embedded variables)
 * Tài liệu tham chiếu giáo dục; nguồn: nginx upstream module docs.
 */
window.NGINX_UPSTREAM_VARS = {
  meta: {
    module: "ngx_http_upstream_module",
    title: "Các biến được nhúng (upstream)",
    version: "1.0.0",
    source: "nginx.org — ngx_http_upstream_module embedded variables",
    summary:
      "Biến nhúng nginx cho upstream: địa chỉ, thời gian, cache, status, cookie/header từ máy chủ thượng nguồn.",
    separatorNote:
      "Nhiều kết nối: giá trị phân tách bằng dấu phẩy. Chuyển hướng nội bộ giữa nhóm (X-Accel-Redirect / error_page): nhóm phân tách bằng dấu hai chấm.",
  },

  variables: [
    {
      id: "upstream_addr",
      name: "$upstream_addr",
      since: null,
      commercial: false,
      category: "address",
      summary:
        "Địa chỉ IP và cổng, hoặc đường dẫn UNIX domain socket của máy chủ thượng nguồn.",
      details: [
        "Nhiều máy chủ trong một request: địa chỉ phân tách bằng dấu phẩy.",
        "Ví dụ: 192.168.1.1:80, 192.168.1.2:80, unix:/tmp/sock",
        "Chuyển hướng nội bộ giữa nhóm (X-Accel-Redirect / error_page): nhóm phân tách bằng dấu hai chấm.",
        "Ví dụ: 192.168.1.1:80, 192.168.1.2:80, unix:/tmp/sock : 192.168.10.1:80, 192.168.10.2:80",
        "Không chọn được máy chủ → biến giữ tên nhóm máy chủ (upstream group name).",
      ],
      related: ["upstream_last_addr", "upstream_status", "upstream_connect_time"],
      logUse: "access_log / error_log — debug backend nào đã xử lý",
    },
    {
      id: "upstream_bytes_received",
      name: "$upstream_bytes_received",
      since: "1.11.4",
      commercial: false,
      category: "bytes",
      summary: "Số byte nhận từ máy chủ thượng nguồn.",
      details: [
        "Nhiều kết nối: phân tách bằng dấu phẩy và dấu hai chấm giống $upstream_addr.",
      ],
      related: ["upstream_bytes_sent", "upstream_response_length", "upstream_addr"],
      logUse: "Đo lưu lượng download từ upstream",
    },
    {
      id: "upstream_bytes_sent",
      name: "$upstream_bytes_sent",
      since: "1.15.8",
      commercial: false,
      category: "bytes",
      summary: "Số byte gửi tới máy chủ thượng nguồn.",
      details: [
        "Nhiều kết nối: phân tách bằng dấu phẩy và dấu hai chấm giống $upstream_addr.",
      ],
      related: ["upstream_bytes_received", "upstream_addr"],
      logUse: "Đo lưu lượng upload tới upstream",
    },
    {
      id: "upstream_cache_status",
      name: "$upstream_cache_status",
      since: "0.8.3",
      commercial: false,
      category: "cache",
      summary: "Trạng thái truy cập bộ nhớ đệm phản hồi upstream.",
      details: [
        "Giá trị: MISS, BYPASS, EXPIRED, STALE, UPDATING, REVALIDATED, HIT.",
      ],
      related: ["upstream_response_time", "upstream_status"],
      logUse: "Phân tích hiệu quả proxy_cache",
      enum: ["MISS", "BYPASS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED", "HIT"],
    },
    {
      id: "upstream_connect_time",
      name: "$upstream_connect_time",
      since: "1.9.1",
      commercial: false,
      category: "timing",
      summary:
        "Thời gian thiết lập kết nối tới upstream (giây, độ phân giải mili giây).",
      details: [
        "Với SSL: bao gồm thời gian handshake.",
        "Nhiều kết nối: phân tách giống $upstream_addr.",
      ],
      related: ["upstream_header_time", "upstream_response_time", "upstream_queue_time"],
      logUse: "Phát hiện upstream chậm kết nối / TLS handshake",
      unit: "seconds (ms resolution)",
    },
    {
      id: "upstream_cookie_",
      name: "$upstream_cookie_name",
      since: "1.7.1",
      commercial: false,
      category: "header",
      dynamic: true,
      summary:
        "Cookie với tên chỉ định do upstream gửi trong Set-Cookie (thay name bằng tên cookie).",
      details: [
        "Chỉ lưu cookie từ phản hồi của máy chủ cuối cùng.",
        "Ví dụ: $upstream_cookie_sessionid",
      ],
      related: ["upstream_http_", "upstream_status"],
      logUse: "Theo dõi session cookie từ backend",
    },
    {
      id: "upstream_header_time",
      name: "$upstream_header_time",
      since: "1.7.10",
      commercial: false,
      category: "timing",
      summary:
        "Thời gian nhận header phản hồi từ upstream (giây, độ phân giải mili giây).",
      details: [
        "Nhiều phản hồi: phân tách giống $upstream_addr.",
      ],
      related: ["upstream_connect_time", "upstream_response_time"],
      logUse: "TTFB phía upstream (tới khi có header)",
      unit: "seconds (ms resolution)",
    },
    {
      id: "upstream_http_",
      name: "$upstream_http_name",
      since: null,
      commercial: false,
      category: "header",
      dynamic: true,
      summary:
        "Trường header phản hồi của upstream (quy tắc chuyển tên giống $http_*).",
      details: [
        "Ví dụ: header Server → $upstream_http_server",
        "Chỉ lưu header từ phản hồi máy chủ cuối cùng.",
      ],
      related: ["upstream_cookie_", "upstream_trailer_"],
      logUse: "Log header quan trọng từ backend (Server, Content-Type…)",
    },
    {
      id: "upstream_last_addr",
      name: "$upstream_last_addr",
      since: "1.29.3",
      commercial: true,
      category: "address",
      summary:
        "IP hoặc đường dẫn UNIX socket của máy chủ thượng nguồn được chọn cuối cùng.",
      details: [
        "Chỉ có trong đăng ký thương mại nginx.",
      ],
      related: ["upstream_addr", "upstream_last_server_name"],
      logUse: "Địa chỉ peer cuối cùng (bản commercial)",
    },
    {
      id: "upstream_last_server_name",
      name: "$upstream_last_server_name",
      since: "1.25.3",
      commercial: true,
      category: "address",
      summary: "Tên máy chủ thượng nguồn được chọn cuối cùng — truyền qua SNI.",
      details: [
        "Ví dụ cấu hình: proxy_ssl_server_name on; proxy_ssl_name $upstream_last_server_name;",
        "Chỉ có trong đăng ký thương mại nginx.",
      ],
      related: ["upstream_last_addr", "upstream_addr"],
      logUse: "SNI đúng tên upstream (bản commercial)",
    },
    {
      id: "upstream_queue_time",
      name: "$upstream_queue_time",
      since: "1.13.9",
      commercial: false,
      category: "timing",
      summary:
        "Thời gian request nằm trong hàng đợi upstream (giây, độ phân giải mili giây).",
      details: [
        "Nhiều phản hồi: phân tách giống $upstream_addr.",
      ],
      related: ["upstream_connect_time", "upstream_response_time"],
      logUse: "Phát hiện nghẽn queue / max_conns",
      unit: "seconds (ms resolution)",
    },
    {
      id: "upstream_response_length",
      name: "$upstream_response_length",
      since: "0.7.27",
      commercial: false,
      category: "bytes",
      summary: "Độ dài phản hồi nhận từ upstream (byte).",
      details: [
        "Nhiều phản hồi: phân tách giống $upstream_addr.",
      ],
      related: ["upstream_bytes_received", "upstream_response_time"],
      logUse: "Kích thước body từ backend",
      unit: "bytes",
    },
    {
      id: "upstream_response_time",
      name: "$upstream_response_time",
      since: null,
      commercial: false,
      category: "timing",
      summary:
        "Thời gian nhận toàn bộ phản hồi từ upstream (giây, độ phân giải mili giây).",
      details: [
        "Nhiều phản hồi: phân tách giống $upstream_addr.",
      ],
      related: ["upstream_connect_time", "upstream_header_time", "upstream_status"],
      logUse: "Latency end-to-end phía upstream",
      unit: "seconds (ms resolution)",
    },
    {
      id: "upstream_status",
      name: "$upstream_status",
      since: null,
      commercial: false,
      category: "status",
      summary: "Mã trạng thái HTTP nhận từ upstream.",
      details: [
        "Nhiều phản hồi: phân tách giống $upstream_addr.",
        "Không chọn được máy chủ → 502 (Bad Gateway).",
      ],
      related: ["upstream_addr", "upstream_response_time", "upstream_cache_status"],
      logUse: "Theo dõi 5xx/4xx từ backend",
    },
    {
      id: "upstream_trailer_",
      name: "$upstream_trailer_name",
      since: "1.13.10",
      commercial: false,
      category: "header",
      dynamic: true,
      summary: "Trường trailer ở cuối phản hồi từ upstream (thay name bằng tên trailer).",
      details: [
        "Áp dụng khi upstream gửi HTTP trailers.",
      ],
      related: ["upstream_http_"],
      logUse: "Đọc trailer (checksum, status phụ…)",
    },
  ],

  /** Gợi ý log_format quan sát upstream */
  logFormatExample: [
    "$remote_addr - $request",
    "upstream=$upstream_addr",
    "status=$upstream_status",
    "rt=$upstream_response_time",
    "uct=$upstream_connect_time",
    "uht=$upstream_header_time",
    "cache=$upstream_cache_status",
  ].join(" "),

  categories: [
    { id: "address", label: "Địa chỉ / peer", icon: "network" },
    { id: "timing", label: "Thời gian", icon: "cpu" },
    { id: "bytes", label: "Byte / độ dài", icon: "hash" },
    { id: "cache", label: "Cache", icon: "spark" },
    { id: "status", label: "Mã trạng thái", icon: "lock" },
    { id: "header", label: "Header / cookie / trailer", icon: "text" },
    { id: "dns", label: "DNS / resolver", icon: "compass" },
    { id: "queue", label: "Hàng đợi upstream", icon: "layers" },
  ],

  /**
   * Chỉ thị (directives) trong context upstream
   */
  directives: [
    {
      id: "resolver",
      name: "resolver",
      kind: "directive",
      syntax:
        "resolver address ... [valid=time] [ipv4=on|off] [ipv6=on|off] [status_zone=zone];",
      default: "—",
      context: ["upstream"],
      since: "1.27.3",
      commercial: false,
      commercialHistory:
        "Từ 1.17.5 đến trước 1.27.3: chỉ có trong đăng ký thương mại. Từ 1.27.3: có trong bản open source (context upstream).",
      category: "dns",
      summary:
        "Cấu hình máy chủ DNS dùng để phân giải tên các máy chủ thượng nguồn thành địa chỉ.",
      details: [
        "Địa chỉ có thể là tên miền hoặc IP, kèm cổng tùy chọn; không ghi cổng → dùng 53.",
        "Nhiều nameserver được truy vấn kiểu round-robin.",
        "Mặc định tra cả IPv4 và IPv6; tắt bằng ipv4=off (≥1.23.1) hoặc ipv6=off.",
        "Mặc định cache câu trả lời theo TTL; valid=time ghi đè TTL cache.",
        "status_zone=zone (≥1.17.5): thu thập thống kê DNS — chỉ commercial.",
        "Nên dùng DNS nội bộ đáng tin cậy để giảm rủi ro DNS spoofing.",
      ],
      examples: [
        "resolver 127.0.0.1 [::1]:5353;",
        "resolver 127.0.0.1 [::1]:5353 valid=30s;",
      ],
      parameters: [
        {
          name: "address",
          required: true,
          summary: "Nameserver (domain/IP) + cổng tùy chọn (mặc định 53)",
        },
        {
          name: "valid",
          required: false,
          summary: "Ghi đè TTL cache câu trả lời DNS (vd valid=30s)",
        },
        {
          name: "ipv4",
          required: false,
          summary: "on|off — bật/tắt tra cứu IPv4 (≥1.23.1)",
          values: ["on", "off"],
        },
        {
          name: "ipv6",
          required: false,
          summary: "on|off — bật/tắt tra cứu IPv6",
          values: ["on", "off"],
        },
        {
          name: "status_zone",
          required: false,
          commercial: true,
          since: "1.17.5",
          summary: "Zone thống kê request/response DNS (commercial)",
        },
      ],
      related: ["upstream_addr", "upstream_last_addr", "upstream_last_server_name"],
      security:
        "Tránh giả mạo DNS: đặt nameserver trong mạng cục bộ tin cậy, đã được bảo vệ đúng.",
      icon: "compass",
    },
    {
      id: "queue",
      name: "queue",
      kind: "directive",
      syntax: "queue number [timeout=time];",
      default: "—",
      context: ["upstream"],
      since: "1.5.12",
      commercial: true,
      commercialHistory:
        "Chỉ có trong đăng ký thương mại nginx (ngx_http_upstream_module).",
      category: "queue",
      summary:
        "Hàng đợi request khi chưa chọn được upstream ngay; giới hạn số request đồng thời trong queue.",
      details: [
        "Nếu không chọn được upstream ngay khi xử lý request → request vào hàng đợi.",
        "number = số request tối đa được xếp hàng cùng lúc.",
        "Queue đầy, hoặc không chọn được server trong timeout → trả client 502 Bad Gateway.",
        "timeout mặc định: 60 giây.",
        "Khi dùng phương thức cân bằng tải khác round-robin mặc định: phải kích hoạt method đó TRƯỚC chỉ thị queue.",
        "Chỉ thị commercial — cần đăng ký thương mại nginx.",
      ],
      examples: [
        "queue 10;",
        "queue 10 timeout=30s;",
      ],
      parameters: [
        {
          name: "number",
          required: true,
          summary: "Số request tối đa trong queue cùng lúc",
        },
        {
          name: "timeout",
          required: false,
          summary: "Thời gian chờ chọn được upstream (mặc định 60s)",
          default: "60s",
        },
      ],
      related: [
        "upstream_queue_time",
        "upstream_addr",
        "upstream_status",
        "resolver",
      ],
      security:
        "Queue đầy / timeout → 502. Theo dõi $upstream_queue_time và health của peer.",
      notes: [
        "Load-balancing method ≠ round-robin phải được bật trước dòng queue.",
      ],
      icon: "layers",
    },
  ],
};
