defmodule MyApp.Accounts do
  @moduledoc """
  Account management for MyApp. Phoenix-style fixture for the #367
  Elixir extractor regression test — diverse enough to exercise
  multi-clause functions (active?/1), guard clauses, pipe operators,
  alias directives, and private functions (defp).
  """

  alias MyApp.Repo
  alias MyApp.Accounts.User

  @max_per_page 50

  def get_user(id), do: Repo.get(User, id)

  def list_active_users do
    User
    |> Repo.all()
    |> Enum.filter(&active?/1)
  end

  def find_by_email(email) when is_binary(email) do
    Repo.get_by(User, email: email)
  end

  defp active?(%User{active: true}), do: true
  defp active?(_), do: false
end
