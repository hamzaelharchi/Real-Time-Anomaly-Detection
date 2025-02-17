from pyspark.sql import SparkSession
from pyspark.sql.functions import col, mean, stddev, lit
import json
from kafka import KafkaProducer
from pyspark.context import SparkContext
from pyspark.sql.session import SparkSession
import time
from pyspark.sql.functions import to_json, from_json, struct


# Initialize Spark
sc = SparkContext('local')
spark = SparkSession(sc)
spark.sparkContext.setLogLevel("ERROR")

# Kafka topic and bootstrap servers
TOPIC_NAME = 'swat'
OUTPUT_TOPIC_NAME = 'swat-zscore'
BOOTSTRAP_SERVERS = "kafka:29092"

# Initialize Kafka Producer
producer = KafkaProducer(
    bootstrap_servers=BOOTSTRAP_SERVERS,
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda v: v.encode('utf-8')
)

# Read from Kafka with Structured Streaming
df = spark \
    .readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", BOOTSTRAP_SERVERS) \
    .option("subscribe", TOPIC_NAME) \
    .option("startingOffsets", "latest") \
    .load()

# Parse the JSON data from Kafka
df_parsed = df.selectExpr("CAST(value AS STRING)").selectExpr("value")
df_json = df_parsed.selectExpr("from_json(value, 'Timestamp STRING, FIT101 DOUBLE, LIT101 DOUBLE, MV101 INT, P101 INT, P102 INT, AIT201 DOUBLE, AIT202 DOUBLE, AIT203 DOUBLE, FIT201 DOUBLE, MV201 INT, P201 INT, P202 INT, P203 INT, P204 INT, P205 INT, P206 INT, DPIT301 DOUBLE, FIT301 DOUBLE, LIT301 DOUBLE, MV301 INT, MV302 INT, MV303 INT, MV304 INT, P301 INT, P302 INT, AIT401 DOUBLE, AIT402 DOUBLE, FIT401 DOUBLE, LIT401 DOUBLE, P401 INT, P402 INT, P403 INT, P404 INT, UV401 INT, AIT501 DOUBLE, AIT502 DOUBLE, AIT503 DOUBLE, AIT504 DOUBLE, FIT501 DOUBLE, FIT502 DOUBLE, FIT503 DOUBLE, FIT504 DOUBLE, P501 INT, P502 INT, PIT501 DOUBLE, PIT502 DOUBLE, PIT503 DOUBLE, FIT601 DOUBLE, P601 INT, P602 INT, P603 INT') AS json")
df_expanded = df_json.select("json.*")

# Function to process each micro-batch
def process_batch(batch_df, batch_id):
    numeric_columns = [field.name for field in batch_df.schema.fields if str(field.dataType) in ["DoubleType", "IntegerType"] and field.name != "Timestamp"]

    for col_name in numeric_columns:
        mean_col = batch_df.agg(mean(col(col_name)).alias(f"mean_{col_name}")).collect()[0][0]
        stddev_col = batch_df.agg(stddev(col(col_name)).alias(f"stddev_{col_name}")).collect()[0][0]

        if stddev_col is not None and stddev_col > 0:
            batch_df = batch_df.withColumn(f"z_{col_name}", (col(col_name) - lit(mean_col)) / lit(stddev_col))
        else:
            batch_df = batch_df.withColumn(f"z_{col_name}", lit(0))

    # Define anomaly based on Z-score threshold
    batch_df = batch_df.withColumn("anomaly", lit(0))
    for col_name in numeric_columns:
        batch_df = batch_df.withColumn("anomaly", col("anomaly") | (col(f"z_{col_name}") > 3) | (col(f"z_{col_name}") < -3))

    # Convert to JSON and send to Kafka
    batch_df = batch_df.withColumn("anomaly", col("anomaly").cast("long"))
    rows = batch_df.toPandas()  # Convert Spark DataFrame to Pandas
    for i, row in rows.iterrows():
        key = f"row-{i}"
        value = json.dumps(row.to_dict())  # Explicitly convert to dictionary
        
        print(f"send {i}", value)
        producer.send(OUTPUT_TOPIC_NAME, key=key, value=value)
        time.sleep(5)

# Write stream using foreachBatch
query = df_expanded \
    .writeStream \
    .outputMode("update") \
    .foreachBatch(process_batch) \
    .start()

# Keep the streaming query running
query.awaitTermination()
