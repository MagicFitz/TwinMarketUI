"""主程序入口：``python -m simulation`` 解析参数并启动模拟系统。"""

import json
import os
import random

import pandas as pd

from simulation.engine import init_db_forum, init_simulation, parse_args


def main() -> None:
    # ============================ 参数解析与验证 ============================
    args = parse_args()
    random.seed(args.random_seed)

    # 确保日志目录存在
    if not os.path.exists(args.log_dir):
        os.makedirs(args.log_dir)
        print(f"创建日志目录: {args.log_dir}")

    # 输出所有配置参数
    print("\n=== 模拟配置参数 ===")
    print(json.dumps(vars(args), indent=4, ensure_ascii=False))

    # ============================ 检查点逻辑判断 ============================
    checkpoint = args.checkpoint
    print("模式: checkpoint 续跑" if checkpoint else "模式: 新建模拟")

    # ============================ 数据库初始化 ============================
    if not checkpoint:
        print("初始化论坛数据库...")
        init_db_forum(db_path=args.forum_db)
        print("论坛数据库初始化完成")

    # ============================ 启动模拟系统 ============================
    print("\n=== 开始运行交易模拟系统 ===")
    init_simulation(
        start_date=pd.Timestamp(args.start_date),
        end_date=pd.Timestamp(args.end_date),
        forum_db=args.forum_db,
        user_db=args.user_db,
        debug=args.debug,
        max_workers=args.max_workers,
        agent_timeout=args.agent_timeout,
        user_graph_save_name=args.user_graph_save_name,
        checkpoint=checkpoint,
        similarity_threshold=args.similarity_threshold,
        time_decay_factor=args.time_decay_factor,
        node=args.node,
        log_dir=args.log_dir,
        prob_of_technical=args.prob_of_technical,
        belief_init_path=args.belief_init_path,
        config_path=args.config_path,
        activate_prob=args.activate_prob,
        checkpoint_state_path=args.checkpoint_state_path,
        runtime_events_path=args.runtime_events,
        run_id=args.run_id,
    )
    print("\n=== 模拟系统运行完成 ===")


if __name__ == "__main__":
    main()
