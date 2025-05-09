from typing import Union
import numpy as np
import time
import tqdm
import os
from gym_idsgame.envs.rendering.video.idsgame_monitor import IdsGameMonitor
from gym_idsgame.agents.training_agents.q_learning.q_agent_config import QAgentConfig
from gym_idsgame.envs.idsgame_env import IdsGameEnv
from gym_idsgame.agents.dao.experiment_result import ExperimentResult
from gym_idsgame.agents.training_agents.q_learning.q_agent import QAgent

class SARSAAgent(QAgent):
    """
    SARSA implementation for the IDSGameEnv, can be used for both attack and defense
    by configuring the appropriate flags in QAgentConfig
    """
    def __init__(self, env:IdsGameEnv, config: QAgentConfig):
        """
        Initialize environment and hyperparameters

        :param env: the environment to train on
        :param config: the hyperparameter configuration
        """
        super(SARSAAgent, self).__init__(env, config)
        
        # Initialize Q-tables exactly as in TabularQAgent
        if not self.config.tab_full_state_space:
            self.Q_attacker = np.zeros((self.env.num_states, self.env.num_attack_actions))
            self.Q_defender = np.zeros((1, self.env.num_defense_actions))
        else:
            self.Q_attacker = np.zeros((self.env.num_states_full, self.env.num_attack_actions))
            self.Q_defender = np.zeros((self.env.num_states_full, self.env.num_defense_actions))
            if self.env.num_states_full < 10000:
                self.state_to_idx = self.env._build_state_to_idx_map()
                self.max_value = np.array(list(map(lambda x: list(x), 
                                                 self.state_to_idx.keys()))).flatten().max()

        # Environment configuration
        self.env.idsgame_config.save_trajectories = False
        self.env.idsgame_config.save_attack_stats = False

    def get_action(self, s, eval=False, attacker=True) -> int:
        """
        Sample an action using epsilon-greedy policy

        :param s: the state to sample an action for
        :param eval: whether sampling an action in eval mode
        :param attacker: if true sample action for attacker, else for defender
        :return: a sampled action
        """
        # Identical to TabularQAgent
        if attacker:
            actions = list(range(self.env.num_attack_actions))
            legal_actions = list(filter(lambda action: self.env.is_attack_legal(action), actions))
        else:
            actions = list(range(self.env.num_defense_actions))
            legal_actions = list(filter(lambda action: self.env.is_defense_legal(action), actions))
            
        if (np.random.rand() < self.config.epsilon and not eval) \
                or (eval and np.random.random() < self.config.eval_epsilon):
            return np.random.choice(legal_actions)
            
        max_legal_action_value = float("-inf")
        max_legal_action = float("-inf")
        
        if attacker:
            for i in range(len(self.Q_attacker[s])):
                if i in legal_actions and self.Q_attacker[s][i] > max_legal_action_value:
                    max_legal_action_value = self.Q_attacker[s][i]
                    max_legal_action = i
        else:
            for i in range(len(self.Q_defender[s])):
                if i in legal_actions and self.Q_defender[s][i] > max_legal_action_value:
                    max_legal_action_value = self.Q_defender[s][i]
                    max_legal_action = i
                    
        if max_legal_action == float("-inf") or max_legal_action_value == float("-inf"):
            raise AssertionError("Error when selecting action greedily according to the Q-function")
            
        return max_legal_action

    def sarsa_update(self, s: int, a: int, r: float, s_prime: int, 
                    a_prime: int, attacker: bool = True) -> None:
        """
        Performs a SARSA update of the Q-values
        
        :param s: the state
        :param a: the action
        :param r: the reward
        :param s_prime: the next state
        :param a_prime: the next action
        :param attacker: whether to update attacker or defender Q-values
        :return: None
        """
        if attacker:
            self.Q_attacker[s][a] = self.Q_attacker[s][a] + self.config.alpha * (
                r + self.config.gamma * self.Q_attacker[s_prime][a_prime] - 
                self.Q_attacker[s][a])
        else:
            self.Q_defender[s][a] = self.Q_defender[s][a] + self.config.alpha * (
                r + self.config.gamma * self.Q_defender[s_prime][a_prime] - 
                self.Q_defender[s][a])

    def step_and_update(self, action, s_idx_a, s_idx_d) -> Union[float, np.ndarray, bool]:
        """
        Takes a step in the environment and updates the Q-table using SARSA
        
        :param action: the action to take
        :param s_idx_a: the attacker state idx
        :param s_idx_d: the defender state idx
        :return: (reward, observation, done)
        """
        obs_prime, reward, done, info = self.env.step(action)
        attacker_reward, defender_reward = reward
        attacker_obs_prime, defender_obs_prime = obs_prime
        attacker_action, defender_action = action

        if self.config.attacker:
            s_prime_idx = self.env.get_attacker_node_from_observation(attacker_obs_prime)
            if self.config.tab_full_state_space:
                if self.env.fully_observed():
                    attacker_obs_prime = np.append(attacker_obs_prime, defender_obs_prime)
                t = tuple(attacker_obs_prime.astype(int).flatten().tolist())
                t = tuple(map(lambda x: min(x, self.max_value), t))
                s_prime_idx = self.state_to_idx[t]
            # Get next action according to epsilon-greedy policy
            a_prime = self.get_action(s_prime_idx, attacker=True)
            # SARSA update
            self.sarsa_update(s_idx_a, attacker_action, attacker_reward, s_prime_idx, 
                            a_prime, attacker=True)

        if self.config.defender:
            s_prime_idx = 0
            if self.config.tab_full_state_space:
                if self.env.fully_observed():
                    defender_obs_prime = np.append(attacker_obs_prime, defender_obs_prime)
                t = tuple(defender_obs_prime.astype(int).flatten().tolist())
                t = tuple(map(lambda x: min(x, self.max_value), t))
                s_prime_idx = self.state_to_idx[t]
            # Get next action according to epsilon-greedy policy
            d_prime = self.get_action(s_prime_idx, attacker=False)
            # SARSA update
            self.sarsa_update(s_idx_d, defender_action, defender_reward, s_prime_idx,
                            d_prime, attacker=False)

        return reward, obs_prime, done
    
    def train(self) -> ExperimentResult:
        """
        Runs the SARSA algorithm

        :return: Experiment result
        """
        self.config.logger.info("Starting Training")
        self.config.logger.info(self.config.to_str())
        if len(self.train_result.avg_episode_steps) > 0:
            self.config.logger.warning("starting training with non-empty result object")
        done = False
        attacker_obs, defender_obs = self.env.reset(update_stats=False)

        # Tracking metrics
        episode_attacker_rewards = []
        episode_defender_rewards = []
        episode_steps = []

        # Logging
        self.outer_train.set_description_str("[Train] epsilon:{:.2f},avg_a_R:{:.2f},avg_d_R:{:.2f},"
                                             "avg_t:{:.2f},avg_h:{:.2f},acc_A_R:{:.2f}," \
                                             "acc_D_R:{:.2f}".format(self.config.epsilon, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

        # Training
        for episode in range(self.config.num_episodes):
            episode_attacker_reward = 0
            episode_defender_reward = 0
            episode_step = 0
            while not done:
                if self.config.render:
                    self.env.render(mode="human")

                if not self.config.attacker and not self.config.defender:
                    raise AssertionError("Must specify whether training an attacker agent or defender agent")

                # Default initialization
                s_idx_a = 0
                defender_state_node_id = 0
                s_idx_d = defender_state_node_id
                attacker_action = 0
                defender_action = 0

                # Get attacker and defender actions
                if self.config.attacker:
                    s_idx_a = self.env.get_attacker_node_from_observation(attacker_obs)
                    if self.config.tab_full_state_space:
                        if self.env.fully_observed():
                            attacker_obs = np.append(attacker_obs, defender_obs)
                        t = tuple(attacker_obs.astype(int).flatten().tolist())
                        t = tuple(map(lambda x: min(x, self.max_value), t))
                        s_idx_a = self.state_to_idx[t]
                    attacker_action = self.get_action(s_idx_a, attacker=True)

                if self.config.defender:
                    s_idx_d = defender_state_node_id
                    if self.config.tab_full_state_space:
                        if self.env.fully_observed():
                            defender_obs = np.append(attacker_obs, defender_obs)
                        t = tuple(defender_obs.astype(int).flatten().tolist())
                        t = tuple(map(lambda x: min(x, self.max_value), t))
                        s_idx_d = self.state_to_idx[t]
                    defender_action = self.get_action(s_idx_d, attacker=False)

                action = (attacker_action, defender_action)

                # Take a step in the environment
                reward, obs_prime, done = self.step_and_update(action, s_idx_a, s_idx_d)

                # Update state information and metrics
                attacker_reward, defender_reward = reward
                obs_prime_attacker, obs_prime_defender = obs_prime
                episode_attacker_reward += attacker_reward
                episode_defender_reward += defender_reward
                episode_step += 1
                attacker_obs = obs_prime_attacker
                defender_obs = obs_prime_defender

            # Render final frame
            if self.config.render:
                self.env.render(mode="human")

            # Record episode metrics
            self.num_train_games += 1
            self.num_train_games_total += 1
            if self.env.state.hacked:
                self.num_train_hacks += 1
                self.num_train_hacks_total += 1
            episode_attacker_rewards.append(episode_attacker_reward)
            episode_defender_rewards.append(episode_defender_reward)
            episode_steps.append(episode_step)

            # Log average metrics every <self.config.train_log_frequency> episodes
            if episode % self.config.train_log_frequency == 0:
                if self.num_train_games > 0 and self.num_train_games_total > 0:
                    self.train_hack_probability = self.num_train_hacks / self.num_train_games
                    self.train_cumulative_hack_probability = self.num_train_hacks_total / self.num_train_games_total
                else:
                    self.train_hack_probability = 0.0
                    self.train_cumulative_hack_probability = 0.0
                self.log_metrics(episode, self.train_result, episode_attacker_rewards, episode_defender_rewards,
                                 episode_steps, None, None, lr=self.config.alpha)
                episode_attacker_rewards = []
                episode_defender_rewards = []
                episode_steps = []
                self.num_train_games = 0
                self.num_train_hacks = 0

            # Run evaluation every <self.config.eval_frequency> episodes
            if episode % self.config.eval_frequency == 0:
                self.eval(episode)

            # Save Q table every <self.config.checkpoint_frequency> episodes
            if episode % self.config.checkpoint_freq == 0:
                self.save_q_table()
                self.env.save_trajectories(checkpoint = True)
                self.env.save_attack_data(checkpoint = True)
                if self.config.save_dir is not None:
                    time_str = str(time.time())
                    self.train_result.to_csv(self.config.save_dir + "/" + time_str + "_train_results_checkpoint.csv")
                    self.eval_result.to_csv(self.config.save_dir + "/" + time_str + "_eval_results_checkpoint.csv")

            # Reset environment for the next episode and update game stats
            done = False
            attacker_obs, defender_obs = self.env.reset(update_stats=True)
            self.outer_train.update(1)

            # Anneal epsilon linearly
            self.anneal_epsilon()

        self.config.logger.info("Training Complete")

        # Final evaluation (for saving Gifs etc)
        self.eval(self.config.num_episodes, log=False)

        # Log and return
        self.log_state_values()

        # Save Q Table
        self.save_q_table()

        # Save other game data
        self.env.save_trajectories(checkpoint = False)
        self.env.save_attack_data(checkpoint = False)
        if self.config.save_dir is not None:
            time_str = str(time.time())
            self.train_result.to_csv(self.config.save_dir + "/" + time_str + "_train_results_checkpoint.csv")
            self.eval_result.to_csv(self.config.save_dir + "/" + time_str + "_eval_results_checkpoint.csv")

        return self.train_result, self.eval_result
    
    def eval(self, train_episode, log=True) -> ExperimentResult:
        """
        Performs evaluation with the greedy policy with respect to the learned SARSA algorithm

        :param log: whether to log the result
        :param train_episode: train episode to keep track of logs and plots
        :return: None
        """
        self.config.logger.info("Starting Evaluation")
        time_str = str(time.time())

        self.num_eval_games = 0
        self.num_eval_hacks = 0

        if len(self.eval_result.avg_episode_steps) > 0:
            self.config.logger.warning("starting eval with non-empty result object")
        if self.config.eval_episodes < 1:
            return
        done = False

        # Video config
        if self.config.video:
            if self.config.video_dir is None:
                raise AssertionError("Video is set to True but no video_dir is provided, please specify "
                                     "the video_dir argument")
            self.env = IdsGameMonitor(self.env, self.config.video_dir + "/" + time_str, force=True,
                                      video_frequency=self.config.video_frequency)
            self.env.metadata["video.frames_per_second"] = self.config.video_fps

        # Tracking metrics
        episode_attacker_rewards = []
        episode_defender_rewards = []
        episode_steps = []

        # Logging
        self.outer_eval = tqdm.tqdm(total=self.config.eval_episodes, desc='Eval Episode', position=1)
        self.outer_eval.set_description_str(
            "[Eval] avg_a_R:{:.2f},avg_d_R:{:.2f},avg_t:{:.2f},avg_h:{:.2f},acc_A_R:{:.2f}," \
            "acc_D_R:{:.2f}".format(0.0, 0,0, 0.0, 0.0, 0.0, 0.0))

        # Eval
        attacker_obs, defender_obs = self.env.reset(update_stats=False)

        # Get initial frame
        if self.config.video or self.config.gifs:
            initial_frame = self.env.render(mode="rgb_array")[0]
            self.env.episode_frames.append(initial_frame)

        for episode in range(self.config.eval_episodes):
            episode_attacker_reward = 0
            episode_defender_reward = 0
            episode_step = 0
            attacker_state_values = []
            attacker_states = []
            attacker_frames = []
            defender_state_values = []
            defender_states = []
            defender_frames = []

            if self.config.video or self.config.gifs:
                attacker_state_node_id = self.env.get_attacker_node_from_observation(attacker_obs)
                attacker_state_values.append(sum(self.Q_attacker[attacker_state_node_id]))
                attacker_states.append(attacker_state_node_id)
                attacker_frames.append(initial_frame)
                defender_state_node_id = 0
                defender_state_values.append(sum(self.Q_defender[defender_state_node_id]))
                defender_states.append(defender_state_node_id)
                defender_frames.append(initial_frame)

            while not done:
                if self.config.eval_render:
                    self.env.render()
                    time.sleep(self.config.eval_sleep)

                # Default initialization
                attacker_state_node_id = 0
                defender_state_node_id = 0
                attacker_action = 0
                defender_action = 0

                # Get attacker and defender actions
                if self.config.attacker:
                    s_idx_a = self.env.get_attacker_node_from_observation(attacker_obs)
                    if self.config.tab_full_state_space:
                        if self.env.fully_observed():
                            attacker_obs = np.append(attacker_obs, defender_obs)
                        t = tuple(attacker_obs.astype(int).flatten().tolist())
                        t = tuple(map(lambda x: min(x, self.max_value), t))
                        s_idx_a = self.state_to_idx[t]
                    attacker_action = self.get_action(s_idx_a, attacker=True, eval=True)

                if self.config.defender:
                    s_idx_d = defender_state_node_id
                    if self.config.tab_full_state_space:
                        if self.env.fully_observed():
                            defender_obs = np.append(attacker_obs, defender_obs)
                        t = tuple(defender_obs.astype(int).flatten().tolist())
                        t = tuple(map(lambda x: min(x, self.max_value), t))
                        s_idx_d = self.state_to_idx[t]
                    defender_action = self.get_action(s_idx_d, attacker=False, eval=True)

                action = (attacker_action, defender_action)

                # Take a step in the environment
                obs_prime, reward, done, _ = self.env.step(action)

                # Update state information and metrics
                attacker_reward, defender_reward = reward
                obs_prime_attacker, obs_prime_defender = obs_prime
                episode_attacker_reward += attacker_reward
                episode_defender_reward += defender_reward
                episode_step += 1
                attacker_obs = obs_prime_attacker
                defender_obs = obs_prime_defender

                # Save state values for analysis later
                if self.config.video and len(self.env.episode_frames) > 1:
                    if self.config.attacker:
                        attacker_state_node_id = self.env.get_attacker_node_from_observation(attacker_obs)
                        attacker_state_values.append(sum(self.Q_attacker[attacker_state_node_id]))
                        attacker_states.append(attacker_state_node_id)
                        attacker_frames.append(self.env.episode_frames[-1])

                    if self.config.defender:
                        defender_state_node_id = 0
                        defender_state_values.append(sum(self.Q_defender[defender_state_node_id]))
                        defender_states.append(defender_state_node_id)
                        defender_frames.append(self.env.episode_frames[-1])

            # Render final frame when game completed
            if self.config.eval_render:
                self.env.render()
                time.sleep(self.config.eval_sleep)
            self.config.logger.info("Eval episode: {}, Game ended after {} steps".format(episode, episode_step))

            # Record episode metrics
            episode_attacker_rewards.append(episode_attacker_reward)
            episode_defender_rewards.append(episode_defender_reward)
            episode_steps.append(episode_step)

            # Update eval stats
            self.num_eval_games +=1
            self.num_eval_games_total += 1
            self.eval_attacker_cumulative_reward += episode_attacker_reward
            self.eval_defender_cumulative_reward += episode_defender_reward
            if self.env.state.hacked:
                self.num_eval_hacks += 1
                self.num_eval_hacks_total += 1

            # Log average metrics every <self.config.eval_log_frequency> episodes
            if episode % self.config.eval_log_frequency == 0 and log:
                if self.num_eval_games > 0:
                    self.eval_hack_probability = float(self.num_eval_hacks) / float(self.num_eval_games)
                if self.num_eval_games_total > 0:
                    self.eval_cumulative_hack_probability = float(self.num_eval_hacks_total) / float(
                        self.num_eval_games_total)
                self.log_metrics(episode, self.eval_result, episode_attacker_rewards, episode_defender_rewards,
                                 episode_steps, update_stats=False, eval = True)

            # Save gifs
            if self.config.gifs and self.config.video:
                self.env.generate_gif(self.config.gif_dir + "/episode_" + str(train_episode) + "_"
                                      + time_str + ".gif", self.config.video_fps)

            if len(attacker_frames) > 1:
                # Save state values analysis for final state
                base_path = self.config.save_dir + "/state_values/" + str(train_episode) + "/"
                if not os.path.exists(base_path):
                    os.makedirs(base_path)
                np.save(base_path + "attacker_states.npy", attacker_states)
                np.save(base_path + "attacker_state_values.npy", attacker_state_values)
                np.save(base_path + "attacker_frames.npy", attacker_frames)


            if len(defender_frames) > 1:
                # Save state values analysis for final state
                base_path = self.config.save_dir + "/state_values/" + str(train_episode) + "/"
                if not os.path.exists(base_path):
                    os.makedirs(base_path)
                np.save(base_path + "defender_states.npy", np.array(defender_states))
                np.save(base_path + "defender_state_values.npy", np.array(defender_state_values))
                np.save(base_path + "defender_frames.npy", np.array(defender_frames))

            # Reset for new eval episode
            done = False
            attacker_obs, defender_obs = self.env.reset(update_stats=False)
            # Get initial frame
            if self.config.video or self.config.gifs:
                initial_frame = self.env.render(mode="rgb_array")[0]
                self.env.episode_frames.append(initial_frame)

            self.outer_eval.update(1)

        # Log average eval statistics
        if log:
            if self.num_eval_games > 0:
                self.eval_hack_probability = float(self.num_eval_hacks) / float(self.num_eval_games)
            if self.num_eval_games_total > 0:
                self.eval_cumulative_hack_probability = float(self.num_eval_hacks_total) / float(self.num_eval_games_total)
            self.log_metrics(train_episode, self.eval_result, episode_attacker_rewards, episode_defender_rewards,
                             episode_steps, update_stats=True, eval=True)

        self.env.close()
        self.config.logger.info("Evaluation Complete")
        return self.eval_result

    def log_state_values(self) -> None:
        """
        Utility function for printing the state-values

        :return: None
        """
        if self.config.attacker:
            self.config.logger.info("--- Attacker State Values ---")
            for i in range(len(self.Q_attacker)):
                state_value = sum(self.Q_attacker[i])
                node_id = i
                self.config.logger.info("s:{},V(s):{}".format(node_id, state_value))
            self.config.logger.info("--------------------")

        if self.config.defender:
            self.config.logger.info("--- Defender State Values ---")
            for i in range(len(self.Q_defender)):
                state_value = sum(self.Q_defender[i])
                node_id = i
                self.config.logger.info("s:{},V(s):{}".format(node_id, state_value))
            self.config.logger.info("--------------------")

    def save_q_table(self) -> None:
        """
        Saves Q table to disk in binary npy format

        :return: None
        """
        time_str = str(time.time())
        if self.config.save_dir is not None:
            if self.config.attacker:
                path = self.config.save_dir + "/" + time_str + "_attacker_q_table.npy"
                self.config.logger.info("Saving Q-table to: {}".format(path))
                np.save(path, self.Q_attacker)
            if self.config.defender:
                path = self.config.save_dir + "/" + time_str + "_defender_q_table.npy"
                self.config.logger.info("Saving Q-table to: {}".format(path))
                np.save(path, self.Q_defender)
        else:
            self.config.logger.warning("Save path not defined, not saving Q table to disk")