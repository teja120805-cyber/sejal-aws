from finops.config import Settings
from finops.repositories.base import Repositories


def build_repositories(settings: Settings) -> Repositories:
    """Demo mode: in-memory store pre-loaded with generated data. Otherwise: DynamoDB."""
    if settings.demo_mode:
        from finops.demo.generator import generate_demo_dataset, load_dataset
        from finops.repositories.memory import create_memory_repositories

        repos = create_memory_repositories()
        load_dataset(repos, generate_demo_dataset())
        return repos

    import boto3

    from finops.repositories.dynamodb import create_dynamodb_repositories

    table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.table_name)
    return create_dynamodb_repositories(table)
