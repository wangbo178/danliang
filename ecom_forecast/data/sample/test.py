import pandas as pd


data = pd.read_parquet('live_schedule.parquet')
data.to_excel('live_schedule.xlsx', index=False)
print(pd.read_excel('live_schedule.xlsx').head())