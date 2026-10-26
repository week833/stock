@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

echo status=fail_closed 1>&2
echo error=shared_data_migration_retired 1>&2
echo canonical=D:\TWStockData\services\market_update\market_update.cmd 1>&2
echo instruction=use_canonical_central_ensure_or_verify 1>&2
exit /b 78
