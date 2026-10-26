# 機器學習與時間序列預測

此分類共有 **12** 個外部來源。每個來源均有獨立資料夾，避免文件、程式與下載內容互相混淆。

| 資料夾 | 原始 repository | 分支 | 用途 |
|---|---|---:|---|
| [qlib](./qlib/) | `microsoft/qlib` | `main` | AI 量化研究、因子、模型訓練、回測與投組流程。 |
| [FinRL](./FinRL/) | `AI4Finance-Foundation/FinRL` | `master` | 深度強化學習交易代理與交易環境。 |
| [darts](./darts/) | `unit8co/darts` | `master` | 統計、機器學習、深度學習與機率式時間序列預測。 |
| [mlforecast](./mlforecast/) | `Nixtla/mlforecast` | `main` | 以 LightGBM、XGBoost、sklearn 進行可擴展時間序列預測。 |
| [neuralforecast](./neuralforecast/) | `Nixtla/neuralforecast` | `main` | N-BEATS、NHITS、TFT、DeepAR 等神經網路 forecasting。 |
| [statsforecast](./statsforecast/) | `Nixtla/statsforecast` | `main` | AutoARIMA、ETS、Theta、MSTL 等統計基準模型。 |
| [LightGBM](./LightGBM/) | `lightgbm-org/LightGBM` | `master` | 高效率梯度提升模型，適合台股分類、回歸與排序。 |
| [xgboost](./xgboost/) | `dmlc/xgboost` | `master` | 梯度提升模型，可與 LightGBM、CatBoost 建立 ensemble。 |
| [catboost](./catboost/) | `catboost/catboost` | `master` | 可原生處理產業、族群與題材等類別特徵。 |
| [optuna](./optuna/) | `optuna/optuna` | `master` | 模型超參數搜尋、試驗剪枝與最佳化。 |
| [shap](./shap/) | `shap/shap` | `master` | 用 SHAP 解釋模型推薦原因與因子貢獻。 |
| [freqtrade](./freqtrade/) | `freqtrade/freqtrade` | `develop` | 交易系統、回測、風控、策略最佳化與 FreqAI 架構參考。 |

實際 clone 的原始碼會存放在：

```txt
repos/machine_learning_forecasting/
```

`repos/` 已由 `.gitignore` 排除，不會誤提交外部原始碼。
