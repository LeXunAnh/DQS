# 📈 DataQuant & Signal — Hệ thống phân tích & tín hiệu giao dịch chứng khoán Việt Nam

DataQuant & Signal là nền tảng thu thập, lưu trữ, tính toán chỉ báo kỹ thuật và phát hiện tín hiệu giao dịch từ dữ liệu thị trường chứng khoán Việt Nam (HOSE, HNX, UPCOM).

Dự án sử dụng **SSI Market Data API** để đồng bộ dữ liệu giá, sau đó tính toán bộ chỉ báo và sinh tín hiệu BUY/SELL dựa trên các chiến lược định lượng.  
Giao diện **Streamlit** giúp trực quan hóa biểu đồ kỹ thuật, lọc tín hiệu và quản lý toàn bộ pipeline dữ liệu.

---

## ✨ Tính năng nổi bật

- **Đồng bộ dữ liệu tự động**
  - Danh mục chứng khoán (HOSE, HNX, UPCOM)
  - Dữ liệu OHLC hàng ngày
  - Dữ liệu giá chi tiết
  - Phát hiện và vá lỗ hổng dữ liệu (Gap Repair)

- **Chỉ báo kỹ thuật (Technical Indicators)**

- **Tín hiệu giao dịch (Signals)**

- **Giao diện người dùng (Streamlit)**

---

## 🛣️ Roadmap

### Phase 1 — Data Infrastructure
- [x] Đồng bộ dữ liệu thị trường
- [x] Lưu trữ PostgreSQL
- [x] Indicator Engine
- [x] Signal Detection Engine
- [x] Streamlit Dashboard

### Phase 2 — Money Flow Analytics
- [x] Theo dõi dòng tiền thị trường
- [x] Phân tích khối ngoại
- [x] Sector Rotation
- [x] Market Breadth Indicators

##STOP PROJECT 

### Phase 3 — Backtesting Framework
- [ ] Backtest chiến lược giao dịch
- [ ] Portfolio Simulation
- [ ] Performance Analytics
- [ ] Risk Metrics

### Phase 4 — Alpha Research
- [ ] Alpha Factor Library
- [ ] Cross-sectional Ranking
- [ ] Multi-factor Models
- [ ] Quant Research Workspace

### Upcoming
- [ ] Add mutiple source data (vnstock,tcbs,...)
- [ ] Real-time Streaming Data
- [ ] Alert System
- [ ] Portfolio Tracking
- [ ] Strategy Marketplace
- [ ] Machine Learning Signals