from src.core.parameter_grid import GridGenerator
from src.core.set_builder import SetFileBuilder
from src.core.base_config import BASE_CONFIG

generator = GridGenerator()
params = generator.generate()

builder = SetFileBuilder(BASE_CONFIG)

# خذ أول تركيبة فقط للاختبار
first = params[0]

dynamic = {
    "InpMAfast": first.ma_fast,
    "InpMAslow": first.ma_slow,
    "InpATR_SL_Mult": first.atr_sl_mult,
    "InpRR": first.rr,
}

builder.save(dynamic, "test_output.set")

print("SET file generated: test_output.set")